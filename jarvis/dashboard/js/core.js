/* J.A.R.V.I.S. — core UI behaviours: bus wiring, core animation, metrics,
   log/notifications, toasts, modals, command input, clock. */
(function (global) {
  "use strict";

  const { get, post } = global.JarvisAPI;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  /* ---------------------------------------------------------------- utils */
  function toast(title, message, level = "info") {
    const host = $("#toasts");
    if (!host) return;
    const el = document.createElement("div");
    el.className = "toast " + level;
    el.innerHTML = `<b></b><span></span>`;
    el.querySelector("b").textContent = title;
    el.querySelector("span").textContent = message;
    host.appendChild(el);
    setTimeout(() => {
      el.classList.add("out");
      setTimeout(() => el.remove(), 320);
    }, 6200);
  }

  function promptModal(title, initial = "") {
    return new Promise((resolve) => {
      const modal = $("#promptModal");
      $("#promptTitle").textContent = title;
      const input = $("#promptInput");
      input.value = initial;
      modal.classList.remove("hidden");
      input.focus();
      input.select();
      const done = (value) => {
        modal.classList.add("hidden");
        input.onkeydown = null;
        $("#promptOk").onclick = null;
        $("#promptCancel").onclick = null;
        resolve(value);
      };
      $("#promptOk").onclick = () => done(input.value.trim());
      $("#promptCancel").onclick = () => done(null);
      input.onkeydown = (e) => {
        if (e.key === "Enter") done(input.value.trim());
        if (e.key === "Escape") done(null);
      };
    });
  }

  function fmtTime(ts) {
    const d = ts ? new Date(ts * 1000) : new Date();
    return d.toLocaleTimeString([], { hour12: false });
  }
  function fmtBytes(gb) { return (gb == null ? "—" : gb.toFixed(1) + " GB"); }
  function clamp(v) { return Math.max(0, Math.min(100, Number(v) || 0)); }

  /* ------------------------------------------------------------- app bus */
  const App = {
    socket: null,
    state: "idle",
    page: "dashboard",
    settings: null,
    metrics: {},
    responsesSeen: 0,
    handlers: {},
    onEvent(type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); },
    emit(type, data) { (this.handlers[type] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } }); },
  };
  global.JarvisUI = { toast, promptModal, fmtTime, fmtBytes, clamp, App, $, $$ };

  /* ------------------------------------------------------------ boot anim */
  function runBoot() {
    const lines = [
      "INITIALISING CORE SYSTEMS…",
      "LOADING TOOL REGISTRY…",
      "CALIBRATING AUDIO PIPELINE…",
      "LINKING NEURAL INTERFACE…",
      "ALL SYSTEMS ONLINE",
    ];
    const host = $("#bootLines");
    lines.forEach((line, i) => {
      const div = document.createElement("div");
      div.textContent = "› " + line;
      div.style.animationDelay = (i * 0.22) + "s";
      host.appendChild(div);
    });
    let pct = 0;
    const arc = $("#bootArc");
    const label = $("#bootPct");
    const started = performance.now();
    const timer = setInterval(() => {
      const elapsed = performance.now() - started;
      // reach 100% within ~1.1 s, but never stall below 88% while waiting
      pct = Math.min(100, Math.max(pct + 9, (elapsed / 1100) * 100));
      label.textContent = Math.round(pct) + "%";
      arc.style.strokeDashoffset = String(553 - (553 * pct) / 100);
      if (pct >= 100) {
        clearInterval(timer);
        setTimeout(() => $("#boot").classList.add("done"), 240);
      }
    }, 70);
  }

  /* ---------------------------------------------------------- core rings */
  function buildTickRing() {
    const ring = $("#tickRing");
    if (!ring) return;
    const ns = "http://www.w3.org/2000/svg";
    for (let i = 0; i < 90; i++) {
      const angle = (i / 90) * Math.PI * 2;
      const major = i % 10 === 0;
      const r1 = 164;
      const r2 = major ? 156 : 160;
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", 200 + Math.cos(angle) * r1);
      line.setAttribute("y1", 200 + Math.sin(angle) * r1);
      line.setAttribute("x2", 200 + Math.cos(angle) * r2);
      line.setAttribute("y2", 200 + Math.sin(angle) * r2);
      if (major) line.setAttribute("class", "major");
      ring.appendChild(line);
    }
  }

  const BARS = 56;
  const WAVE_RADIUS = 88;

  /* Radial sound-wave visualiser: lines growing outward from the core.
     Built once, then only end-points are animated (cheap + geometry-safe). */
  function buildWaveform() {
    const group = $("#waveform");
    if (!group) return;
    const ns = "http://www.w3.org/2000/svg";
    for (let i = 0; i < BARS; i++) {
      const angle = (i / BARS) * Math.PI * 2;
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", (200 + Math.cos(angle) * WAVE_RADIUS).toFixed(2));
      line.setAttribute("y1", (200 + Math.sin(angle) * WAVE_RADIUS).toFixed(2));
      line.setAttribute("x2", (200 + Math.cos(angle) * WAVE_RADIUS).toFixed(2));
      line.setAttribute("y2", (200 + Math.sin(angle) * WAVE_RADIUS).toFixed(2));
      line.setAttribute("stroke", "var(--accent)");
      line.setAttribute("stroke-width", "2.2");
      line.setAttribute("stroke-linecap", "round");
      line.setAttribute("opacity", "0.25");
      line.dataset.angle = String(angle);
      group.appendChild(line);
    }
  }

  let wavePhase = 0;
  let micLevel = 0;

  function animateWaveform() {
    const group = $("#waveform");
    if (!group || !state_.animations) return;
    const bars = group.children;
    const active = ["listening", "speaking", "thinking", "executing", "wake_listening"]
      .includes(App.state);
    wavePhase += 0.14;
    for (let i = 0; i < bars.length; i++) {
      const bar = bars[i];
      const angle = Number(bar.dataset.angle);
      const ripple = Math.abs(Math.sin(wavePhase + i * 0.35));
      const length = active ? 4 + ripple * 26 + micLevel * 40 * ripple : 3;
      const outer = WAVE_RADIUS + length;
      bar.setAttribute("x2", (200 + Math.cos(angle) * outer).toFixed(2));
      bar.setAttribute("y2", (200 + Math.sin(angle) * outer).toFixed(2));
      bar.setAttribute("opacity", active ? (0.4 + Math.min(length / 45, 0.6)).toFixed(2) : "0.18");
    }
  }

  /* ----------------------------------------------------------- state text */
  const STATE_COPY = {
    idle: ["STANDBY", "awaiting instruction"],
    wake_listening: ["LISTENING", "wake word armed — say “Hey JARVIS”"],
    listening: ["LISTENING", "capturing your command"],
    thinking: ["PROCESSING", "consulting the neural core"],
    speaking: ["SPEAKING", "voice synthesis active"],
    executing: ["EXECUTING", "running computer control"],
    alert: ["ALERT", "attention required"],
  };

  function setState(state) {
    App.state = state;
    const wrap = $("#coreWrap");
    if (wrap) {
      wrap.dataset.state = state === "wake_listening" ? "listening" : state;
      const [label, meta] = STATE_COPY[state] || STATE_COPY.idle;
      $("#coreState").textContent = label;
      $("#coreMeta").textContent = meta;
    }
    const micPill = $("#micPill");
    if (micPill) {
      micPill.textContent = "MIC · " + state.replace("wake_listening", "armed");
      micPill.classList.toggle("live", state === "listening" || state === "wake_listening");
    }
  }

  /* -------------------------------------------------------------- metrics */
  function updateMetrics(m) {
    App.metrics = m || {};
    const cpu = (m.cpu || {});
    setMetric("cpu", cpu.percent, `${cpu.cores_logical || "?"} logical cores`);
    const perCore = $("#perCore");
    if (perCore && Array.isArray(cpu.per_core)) {
      if (perCore.children.length !== cpu.per_core.length) {
        perCore.innerHTML = "";
        cpu.per_core.forEach(() => perCore.appendChild(document.createElement("i")));
      }
      cpu.per_core.forEach((pct, i) => {
        perCore.children[i].style.setProperty("--h", clamp(pct) + "%");
      });
      const big = $("#perCoreBig");
      if (big) {
        if (big.children.length !== cpu.per_core.length) {
          big.innerHTML = "";
          cpu.per_core.forEach(() => big.appendChild(document.createElement("i")));
        }
        cpu.per_core.forEach((pct, i) => big.children[i].style.setProperty("--h", clamp(pct) + "%"));
      }
    }

    const mem = m.memory || {};
    setMetric("memory", mem.percent, mem.total_gb != null
      ? `${mem.used_gb} / ${mem.total_gb} GB` : "unavailable");

    const gpu = m.gpu || {};
    setMetric("gpu", gpu.available ? gpu.percent : null,
      gpu.available ? `${gpu.name} · ${gpu.memory_used_mb}/${gpu.memory_total_mb} MB · ${gpu.temperature_c}°C`
                    : "no GPU telemetry");

    const battery = m.battery || {};
    setMetric("battery", battery.available ? battery.percent : null,
      battery.available ? (battery.plugged ? "charging / plugged in" : "on battery") : "no battery");

    const disks = m.disk || [];
    const main = disks[0];
    setMetric("disk", main ? main.percent : null,
      main ? `${main.free_gb} GB free of ${main.total_gb} GB` : "unavailable");

    const net = m.network || {};
    setText("#netVal", net.online ? "ONLINE" : "OFFLINE");
    setText("#uptimeVal", (m.uptime || {}).human || "—");
    setText("#hostVal", (m.platform || {}).hostname || "—");
    setText("#osVal", ((m.platform || {}).system || "") + " " + ((m.platform || {}).release || ""));
    App.emit("metrics", m);
  }

  function setText(sel, value) {
    const el = $(sel);
    if (el) el.textContent = value;
  }

  const METRIC_IDS = { cpu: "cpu", memory: "mem", gpu: "gpu", battery: "bat", disk: "disk" };

  function setMetric(name, percent, sub) {
    const prefix = METRIC_IDS[name] || name;
    const bar = $(`#${prefix}Bar`);
    const val = $(`#${prefix}Val`);
    const subEl = $(`#${prefix}Sub`);
    if (val) val.textContent = percent == null ? "n/a" : Math.round(percent) + "%";
    if (bar) {
      const pct = clamp(percent);
      bar.style.width = (percent == null ? 0 : pct) + "%";
      bar.classList.toggle("warn", pct >= 70 && pct < 88);
      bar.classList.toggle("crit", pct >= 88);
    }
    if (subEl && sub != null) subEl.textContent = sub;
  }

  /* ---------------------------------------------------------- logs/notifs */
  function addLog(entry) {
    const list = $("#logList");
    if (!list) return;
    const el = document.createElement("div");
    el.className = "log-item " + (entry.level || "info");
    const time = new Date().toLocaleTimeString([], { hour12: false });
    el.innerHTML = `<b>${time}</b> `;
    el.appendChild(document.createTextNode(entry.message || ""));
    list.appendChild(el);
    while (list.children.length > 120) list.removeChild(list.firstChild);
    list.scrollTop = list.scrollHeight;
  }

  function addNotification(entry) {
    const list = $("#notifList");
    if (!list) return;
    const el = document.createElement("div");
    el.className = "notif " + (entry.level || "info");
    el.innerHTML = `<b></b><span></span>`;
    el.querySelector("b").textContent = entry.title || "Notice";
    el.querySelector("span").textContent = entry.message || "";
    list.prepend(el);
    while (list.children.length > 20) list.removeChild(list.lastChild);
  }

  /* ------------------------------------------------------------ chat UI */
  function addMessage(role, text, meta) {
    const chat = $("#chat");
    if (!chat) return;
    const el = document.createElement("div");
    el.className = "msg " + role;
    el.textContent = text;
    if (meta) {
      const m = document.createElement("span");
      m.className = "meta";
      m.textContent = meta;
      el.appendChild(m);
    }
    chat.appendChild(el);
    chat.scrollTop = chat.scrollHeight;
  }

  /* ------------------------------------------------------------- commands */
  function renderReply(reply) {
    if (!reply || !reply.text) return;
    const meta = [reply.provider, reply.model].filter(Boolean).join(" · ");
    addMessage("assistant", reply.text, meta);
    (reply.tool_calls || []).forEach((call) =>
      addMessage("tool", `${call.ok === false ? "✕" : "✓"} ${call.name}`));
  }
  async function sendCommand(text, speak = true) {
    text = (text || "").trim();
    if (!text) return;
    addMessage("user", text);
    addLog({ message: "You: " + text, level: "info" });
    setState("thinking");
    try {
      const before = App.responsesSeen;
      const result = await post("/command", { text, speak });
      const reply = (result && result.data) || {};
      // Normally the WebSocket echo renders the reply. If the event has not
      // arrived (socket reconnecting, or a very fast REST response) render it
      // from the REST payload so a reply is never silently lost.
      const socketRendered = App.responsesSeen > before;
      if (!socketRendered) {
        if (reply.text) renderReply(reply);
        if (App.socket && App.socket.connected) App.responsesSeen = before + 1;
      }
      setState("idle");
      return reply;
    } catch (error) {
      setState("idle");
      toast("Command failed", error.message, "error");
      addMessage("assistant", "I could not process that: " + error.message);
      return null;
    }
  }

  /* ------------------------------------------------------ permission flow */
  function showPermission(request) {
    const modal = $("#permModal");
    $("#permText").textContent = request.description || request.action;
    $("#permTitle").textContent = (request.risk === "high" ? "High-risk action" : "Confirmation required");
    modal.classList.remove("hidden");
    setState("alert");
    App.pendingPermission = request;
    $("#permAllow").onclick = async () => {
      modal.classList.add("hidden");
      setState("executing");
      try {
        await post(`/permissions/${request.id}`, { approved: true, remember: $("#permRemember").checked });
      } catch (e) { toast("Approval failed", e.message, "error"); }
      App.pendingPermission = null;
    };
    $("#permDeny").onclick = async () => {
      modal.classList.add("hidden");
      setState("idle");
      try {
        await post(`/permissions/${request.id}`, { approved: false });
      } catch (e) { /* expired */ }
      App.pendingPermission = null;
    };
  }

  /* --------------------------------------------------------------- clock */
  function startClock() {
    const tick = () => {
      const now = new Date();
      setText("#clock", now.toLocaleTimeString([], { hour12: false }));
    };
    tick();
    setInterval(tick, 1000);
  }

  /* ------------------------------------------------- initialise animations */
  function initCore() {
    buildTickRing();
    buildWaveform();
    setInterval(animateWaveform, 70);
  }

  /* ------------------------------------------------------- wiring events */
  function wire(socket) {
    App.socket = socket;
    socket.on("state", (data) => {
      if (!data) return;
      if (data.state) setState(data.state);
      if (data.voice_mode && data.voice_mode !== "idle") {
        setText("#wakeStatus", "wake word: " + data.voice_mode);
      }
    });
    socket.on("metrics", updateMetrics);
    socket.on("log", addLog);
    socket.on("notification", (data) => {
      addNotification(data);
      toast(data.title || "Notification", data.message || "", data.level || "info");
    });
    socket.on("response", (data) => {
      App.responsesSeen = (App.responsesSeen || 0) + 1;
      renderReply(data);
      setState("idle");
      App.emit("response", data);
    });
    socket.on("transcript", (data) => {
      if (data && data.text) addLog({ message: "Heard: " + data.text, level: "info" });
      App.emit("transcript", data);
    });
    socket.on("permission", (data) => {
      if (!data) return;
      if (data.event === "request") showPermission(data);
      if (data.event === "resolved") {
        $("#permModal").classList.add("hidden");
        if (App.pendingPermission && App.pendingPermission.id === data.id) App.pendingPermission = null;
        if (App.state === "alert") setState("idle");
      }
    });
    socket.on("task", (data) => App.emit("task", data));
    socket.on("audio_level", (data) => {
      if (data && typeof data.level === "number") micLevel = Math.min(1, data.level * 14);
    });
    socket.on("error", (data) => {
      if (data && data.message) toast("System error", data.message, "error");
    });
    socket.on("_connection", (data) => {
      const pill = $("#aiPill");
      if (data.connected) {
        if (!App.hadConnection) {
          App.hadConnection = true;
        }
      } else {
        addLog({ message: "Dashboard link lost — reconnecting…", level: "warning" });
      }
    });
  }

  const state_ = { animations: true };

  function setAnimations(enabled) {
    state_.animations = enabled;
    document.body.classList.toggle("no-anim", !enabled);
  }
  function setScanlines(enabled) {
    document.body.classList.toggle("no-scan", !enabled);
  }
  function setAccent(color) {
    if (color) document.documentElement.style.setProperty("--accent", color);
  }

  global.JarvisCore = {
    toast, promptModal, App, setState, updateMetrics, addLog, addNotification,
    addMessage, sendCommand, wire, initCore, startClock, runBoot,
    setAnimations, setScanlines, setAccent, setText, fmtTime, clamp,
  };
})(window);
