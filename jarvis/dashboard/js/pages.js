/* J.A.R.V.I.S. — page controllers. Every control here hits a real endpoint. */
(function (global) {
  "use strict";

  const { get, post, put, del, query } = global.JarvisAPI;
  const Core = () => global.JarvisCore;
  const $ = (s, r = document) => r.querySelector(s);

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  /* ==================================================================== */
  /* Page router                                                          */
  /* ==================================================================== */
  const routes = {};
  const loaded = {};

  function initNav() {
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      btn.onclick = () => navigate(btn.dataset.page);
    });
  }

  function navigate(page) {
    document.querySelectorAll(".nav-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.page === page));
    document.querySelectorAll(".page").forEach((p) =>
      p.classList.toggle("active", p.id === "page-" + page));
    Core().App.page = page;
    const handler = routes[page];
    if (handler) handler(!loaded[page]);
    loaded[page] = true;
  }

  /* ==================================================================== */
  /* Dashboard page                                                       */
  /* ==================================================================== */
  async function loadDashboard(force) {
    try {
      const res = await get("/health");
      const d = res.data;
      const ai = d.ai || {};
      const pill = $("#aiPill");
      pill.textContent = "AI · " + (ai.provider || "?") + (ai.online ? " ✓" : " ✕");
      pill.className = "pill " + (ai.online ? "online" : "offline");
      pill.title = ai.detail || "";
      setText("#ttsStatus", "voice: " + ((d.speech || {}).engine || "none")
        + (d.speech && d.speech.speaking ? " · speaking" : ""));
      const wake = (d.voice || {}).wake_word || {};
      setText("#wakeStatus", "wake word: " + ((d.voice || {}).mode || "idle")
        + (wake.available ? " ✓" : " ✕"));
    } catch (e) {
      const pill = $("#aiPill");
      pill.textContent = "AI · unreachable";
      pill.className = "pill offline";
    }
  }

  async function refreshMetrics() {
    try {
      const res = await get("/metrics");
      Core().updateMetrics(res.data);
    } catch (e) { /* the WS stream is the primary source */ }
  }

  /* ==================================================================== */
  /* Assistant page                                                       */
  /* ==================================================================== */
  async function loadAssistant() {
    try {
      const [health, tools, history] = await Promise.all([
        get("/health"), get("/tools"), get("/conversation"),
      ]);
      const ai = health.data.ai || {};
      const kv = $("#aiInfo");
      kv.innerHTML = "";
      const rows = [
        ["Provider", ai.provider || "—"],
        ["Status", ai.online ? "online" : "offline"],
        ["Model", (health.data.ai && "") + (ai.models && ai.models.length ? ai.models[0] : "") || "—"],
        ["Detail", ai.detail || "—"],
        ["Tools", String(health.data.tools || 0)],
        ["Platform", health.data.platform || "—"],
      ];
      rows.forEach(([k, v]) => {
        const dt = el("dt", null, k); const dd = el("dd", null, v);
        kv.append(dt, dd);
      });

      const grid = $("#toolGrid");
      grid.innerHTML = "";
      (tools.data.tools || []).forEach((tool) => {
        const card = el("div", "tool-card");
        card.appendChild(el("b", null, tool.name));
        card.appendChild(el("span", null, tool.description.slice(0, 90)));
        if (tool.risk === "high" || tool.requires_confirmation) {
          card.appendChild(el("span", "risk" + (tool.risk === "high" ? " high" : ""),
            tool.risk === "high" ? "HIGH RISK" : "CONFIRM"));
        }
        card.onclick = () => {
          Core().toast(tool.name, tool.description, "info");
          $("#chatInput").value = "";
        };
        grid.appendChild(card);
      });
      $("#toolCount").textContent = "(" + (tools.data.tools || []).length + ")";

      const chat = $("#chat");
      chat.innerHTML = "";
      (history.data.turns || []).slice(-30).forEach((turn) => {
        (global.JarvisCore.addMessage)(turn.role === "assistant" ? "assistant" : "user",
          turn.content);
      });
    } catch (e) { Core().toast("Assistant", e.message, "error"); }
  }

  /* ==================================================================== */
  /* System page                                                          */
  /* ==================================================================== */
  async function loadSystem() {
    try {
      const res = await get("/system");
      const d = res.data;
      const kv = $("#sysKv");
      kv.innerHTML = "";
      const rows = [
        ["CPU", (d.cpu.percent || 0).toFixed(0) + "% · " + (d.cpu.cores_logical || "?") + " threads"],
        ["Memory", `${d.memory.used_gb} / ${d.memory.total_gb} GB (${d.memory.percent}%)`],
        ["GPU", d.gpu.available ? `${d.gpu.name} · ${d.gpu.percent}% · ${d.gpu.temperature_c}°C` : "not available"],
        ["Battery", d.battery.available ? `${d.battery.percent}% ${d.battery.plugged ? "(charging)" : ""}` : "none"],
        ["Disk", d.disk[0] ? `${d.disk[0].free_gb} GB free of ${d.disk[0].total_gb} GB` : "—"],
        ["Network", (d.network.online ? "online · " : "offline · ") + d.network.local_ip],
        ["Uptime", d.uptime.human],
        ["Host", d.platform.hostname + " · " + d.platform.system + " " + d.platform.release],
      ];
      rows.forEach(([k, v]) => kv.append(el("dt", null, k), el("dd", null, v)));

      const tbody = $("#procTable tbody");
      tbody.innerHTML = "";
      (d.processes || []).forEach((p) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${p.name}</td><td class="dim">${p.cpu}%</td><td class="dim">${p.memory_percent}%</td>`;
        tbody.appendChild(tr);
      });
      Core().updateMetrics(d);
    } catch (e) { Core().toast("System", e.message, "error"); }
  }

  async function runSystemAction(action) {
    try {
      if (action === "lock") {
        const res = await post("/tools/invoke", { name: "lock_workstation", arguments: {} });
        Core().toast("System", res.message || "Done", res.data && res.data.ok === false ? "error" : "info");
      } else if (action === "taskmgr") {
        const res = await post("/tools/invoke", { name: "open_task_manager", arguments: {} });
        Core().toast("System", res.message, "info");
      } else if (action === "health") {
        const res = await post("/tools/invoke", { name: "health_check", arguments: {} });
        Core().toast("Health check", res.message, "info");
      } else if (action === "screenshot") {
        const res = await post("/vision/screenshot", {});
        Core().toast("Screenshot", res.message || "captured", "info");
      }
    } catch (e) { Core().toast("Action failed", e.message, "error"); }
  }

  async function runPower(action) {
    try {
      const res = await post("/tools/invoke",
        { name: "power_action", arguments: { action } });
      Core().toast("Power", res.message || "requested",
        res.data && res.data.ok === false ? "error" : "warning");
    } catch (e) { Core().toast("Power action", e.message, "error"); }
  }

  async function runPowershell() {
    const command = $("#psInput").value.trim();
    if (!command) return;
    $("#psOutput").textContent = "Running…";
    try {
      const res = await get(`/system/powershell?command=${encodeURIComponent(command)}`);
      const payload = res.data || {};
      const out = payload.data || {};
      $("#psOutput").textContent =
        (out.stdout || "") + (out.stderr ? "\n[stderr]\n" + out.stderr : "") ||
        payload.message || "(no output)";
    } catch (e) {
      $("#psOutput").textContent = "Blocked or failed: " + e.message;
      Core().toast("PowerShell", e.message, "error");
    }
  }

  /* ==================================================================== */
  /* Applications page                                                    */
  /* ==================================================================== */
  let appCache = { installed: [], running: [] };

  async function loadApplications() {
    try {
      const res = await get("/apps");
      appCache = res.data;
      renderApps();
      renderWindows();
      const tbody = $("#runTable tbody");
      tbody.innerHTML = "";
      (appCache.running || []).forEach((proc) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${proc.name}</td><td class="dim">${proc.memory_percent}%</td>`;
        const td = document.createElement("td");
        td.className = "acts";
        const btn = el("button", "mini-btn", "close");
        btn.onclick = async () => {
          const res2 = await post("/tools/invoke",
            { name: "close_application", arguments: { name: proc.name } });
          Core().toast("Close " + proc.name, res2.message, res2.data && res2.data.ok === false ? "error" : "info");
          loadApplications();
        };
        td.appendChild(btn);
        tr.appendChild(td);
        tbody.appendChild(tr);
      });
    } catch (e) { Core().toast("Applications", e.message, "error"); }
  }

  function renderApps() {
    const filter = ($("#appFilter").value || "").toLowerCase();
    const grid = $("#appGrid");
    grid.innerHTML = "";
    (appCache.installed || [])
      .filter((name) => name.toLowerCase().includes(filter))
      .slice(0, 160)
      .forEach((name) => {
        const tile = el("div", "app-tile");
        tile.appendChild(el("span", "glyph", "◈"));
        tile.appendChild(document.createTextNode(name));
        tile.onclick = async () => {
          try {
            const res = await post("/apps/launch", { name });
            Core().toast("Launch", res.message, res.data && res.data.ok === false ? "error" : "info");
          } catch (e) { Core().toast("Launch failed", e.message, "error"); }
        };
        grid.appendChild(tile);
      });
  }

  async function renderWindows() {
    try {
      const res = await post("/tools/invoke",
        { name: "list_windows", arguments: {} });
      const list = $("#winList");
      list.innerHTML = "";
      ((res.data && res.data.data && res.data.data.windows) || []).slice(0, 14).forEach((win) => {
        const item = el("div", "win-item");
        item.appendChild(el("span", null, win.title));
        const btn = el("button", "mini-btn", "focus");
        btn.onclick = async () => {
          const r = await post("/tools/invoke",
            { name: "focus_application", arguments: { name: win.title } });
          Core().toast("Focus", r.message, "info");
        };
        item.appendChild(btn);
        list.appendChild(item);
      });
    } catch (e) { /* window tools need Windows */ }
  }

  /* ==================================================================== */
  /* Files page                                                           */
  /* ==================================================================== */
  let currentPath = "~";
  let selectedPath = null;
  const pathHistory = [];

  async function loadFiles(path) {
    try {
      const res = await get("/files/list" + query({ path: path || currentPath }));
      const d = res.data;
      currentPath = d.path;
      $("#fsPath").value = d.path;
      selectedPath = null;
      const tbody = $("#fsTable tbody");
      tbody.innerHTML = "";
      (d.entries || []).forEach((entry) => {
        const tr = document.createElement("tr");
        const nameTd = el("td", "clickable", (entry.is_dir ? "▸ " : "· ") + entry.name);
        nameTd.onclick = () => {
          markSelected(tr);
          if (entry.is_dir) loadFiles(entry.path);
          else selectFile(entry);
        };
        tr.appendChild(nameTd);
        tr.appendChild(el("td", "dim", entry.size_kb != null ? entry.size_kb + " KB" : "<dir>"));
        tr.appendChild(el("td", "dim", entry.modified));
        tr.onclick = (event) => {
          if (event.target !== nameTd) {
            markSelected(tr);
            selectFile(entry);
          }
        };
        tbody.appendChild(tr);
      });
      $("#fsOutput").textContent = `${d.entries.length} item(s) in ${d.path}`;
      $("#fsPath").dataset.parent = d.parent;
    } catch (e) { Core().toast("File manager", e.message, "error"); }
  }

  let selectedRow = null;

  function markSelected(row) {
    if (selectedRow) selectedRow.classList.remove("selected");
    selectedRow = row;
    if (row) row.classList.add("selected");
  }

  function selectFile(entry) {
    selectedPath = entry.path;
    $("#fsOutput").textContent =
      `Selected: ${entry.name}\nPath: ${entry.path}\n` +
      `Size: ${entry.size_kb != null ? entry.size_kb + " KB" : "directory"}\n` +
      `Modified: ${entry.modified}`;
  }

  async function fileAction(operation, paths, options) {
    try {
      const res = await post("/files/action", { operation, paths: paths || {}, options: options || {} });
      Core().toast(operation, res.message, res.data && res.data.ok === false ? "error" : "info");
      $("#fsOutput").textContent = res.message;
      loadFiles();
      return res;
    } catch (e) {
      Core().toast(operation, e.message, "error");
      $("#fsOutput").textContent = e.message;
      return null;
    }
  }

  async function searchFiles() {
    const q = $("#fsQuery").value.trim();
    if (!q) return;
    try {
      const res = await get("/files/search" + query({ q, root: currentPath }));
      const tbody = $("#fsResults tbody");
      tbody.innerHTML = "";
      ((res.data || {}).results || []).forEach((item) => {
        const tr = document.createElement("tr");
        const td = el("td", "clickable", item.name + "  (" + item.size_kb + " KB)");
        td.onclick = () => { $("#fsOutput").textContent = item.path; };
        tr.appendChild(td);
        tbody.appendChild(tr);
      });
      $("#fsOutput").textContent = res.message;
    } catch (e) { Core().toast("Search", e.message, "error"); }
  }

  /* ==================================================================== */
  /* Automation page                                                      */
  /* ==================================================================== */
  function renderTasks(tasks, active) {
    const list = $("#taskList");
    list.innerHTML = "";
    const all = [];
    if (active) all.push(active);
    (tasks || []).forEach((t) => { if (!active || t.task_id !== active.task_id) all.push(t); });
    if (!all.length) { list.appendChild(el("div", "muted", "No tasks queued.")); return; }
    all.slice(0, 12).forEach((task) => {
      const card = el("div", "task-card");
      const head = el("div", "head");
      head.appendChild(el("span", null, task.title));
      head.appendChild(el("span", "state " + (task.status || ""), (task.status || "").toUpperCase()));
      card.appendChild(head);
      const barWrap = el("div", "task-bar");
      const bar = el("i");
      bar.style.width = Math.round((task.progress || 0) * 100) + "%";
      barWrap.appendChild(bar);
      card.appendChild(barWrap);
      card.appendChild(el("div", "detail",
        `${task.detail || ""} · ${(task.payload && task.payload.steps ? task.payload.steps.length : 0)} step(s)`));
      list.appendChild(card);
      const barEl = $("#taskBar");
      if (barEl && task.status === "running") {
        barEl.style.width = Math.round((task.progress || 0) * 100) + "%";
        setText("#taskText", task.title);
      }
    });
    if (!active) {
      const barEl = $("#taskBar");
      if (barEl) barEl.style.width = "0%";
      setText("#taskText", "idle");
    }
  }

  function setText(sel, v) { const n = $(sel); if (n) n.textContent = v; }

  async function loadAutomation() {
    try {
      const res = await get("/tasks");
      renderTasks(res.data.tasks, res.data.active);
    } catch (e) { /* ignore */ }
    try {
      const res = await get("/automation/commands");
      const list = $("#ccList");
      list.innerHTML = "";
      (res.data.commands || []).forEach((cmd) => {
        const item = el("div", "cc-item");
        item.appendChild(el("b", null, cmd.trigger));
        const steps = ((cmd.action || {}).steps || []).length;
        item.appendChild(el("small", null, `${steps} step(s)${cmd.enabled ? "" : " · disabled"}`));
        item.appendChild(el("div", "spacer"));
        const toggle = el("button", "mini-btn", cmd.enabled ? "disable" : "enable");
        toggle.onclick = async () => {
          await post(`/automation/commands/${cmd.id}/toggle${query({ enabled: !cmd.enabled })}`);
          loadAutomation();
        };
        const remove = el("button", "mini-btn", "delete");
        remove.onclick = async () => {
          await del(`/automation/commands/${cmd.id}`);
          loadAutomation();
        };
        item.append(toggle, remove);
        list.appendChild(item);
      });
    } catch (e) { /* ignore */ }
  }

  async function planGoal() {
    const goal = $("#goalInput").value.trim();
    if (!goal) return;
    $("#planOutput").textContent = "Planning…";
    try {
      const res = await get("/automation/plan" + query({ goal }));
      const steps = (res.data.steps || []);
      $("#planOutput").textContent = (res.data.source === "workflow"
        ? "Workflow: " + goal + "\n\n"
        : "AI plan for: " + goal + "\n\n")
        + steps.map((s, i) => `${i + 1}. ${s.description || s.tool} → ${s.tool}(${JSON.stringify(s.arguments || {})})`).join("\n")
        + (steps.length ? "\n\nReview, then press Run." : "\n\n" + (res.data.message || "No plan."));
    } catch (e) {
      $("#planOutput").textContent = "Planning failed: " + e.message;
    }
  }

  async function runGoal() {
    const goal = $("#goalInput").value.trim();
    if (!goal) return;
    try {
      const res = await get("/automation/run" + query({ goal }));
      Core().toast("Queued", goal, "info");
      loadAutomation();
      navigate("automation");
    } catch (e) { Core().toast("Run failed", e.message, "error"); }
  }

  /* ==================================================================== */
  /* Memory page                                                          */
  /* ==================================================================== */
  async function loadMemory() {
    try {
      const [res, history] = await Promise.all([get("/memory"), get("/conversation")]);
      $("#memEnabled").checked = !!res.data.enabled;
      const tbody = $("#memTable tbody");
      tbody.innerHTML = "";
      (res.data.items || []).forEach((item) => {
        const tr = document.createElement("tr");
        tr.appendChild(el("td", null, item.key));
        tr.appendChild(el("td", "dim", item.value));
        tr.appendChild(el("td", "dim", item.category));
        const td = el("td", "acts");
        const remove = el("button", "mini-btn", "✕");
        remove.onclick = async () => {
          await del(`/memory/${item.id}`);
          loadMemory();
        };
        td.appendChild(remove);
        tr.appendChild(td);
        tbody.appendChild(tr);
      });

      const chat = $("#historyChat");
      chat.innerHTML = "";
      ((history.data || {}).turns || []).slice(-40).forEach((t) => {
        (global.JarvisCore.addMessage)(t.role === "assistant" ? "assistant" : "user", t.content);
      });
    } catch (e) { Core().toast("Memory", e.message, "error"); }
  }

  /* ==================================================================== */
  /* Settings page                                                        */
  /* ==================================================================== */
  async function loadSettings() {
    try {
      const [res, devices, audit, health] = await Promise.all([
        get("/settings"), get("/audio/devices"), get("/audit"), get("/health"),
      ]);
      const s = res.data;
      Core().App.settings = s;

      $("#setProvider").value = s.ai.provider;
      $("#setModel").value = s.ai.model;
      $("#setTemp").value = s.ai.temperature;
      $("#setCtx").value = s.ai.context_turns;

      const mic = $("#setMic");
      mic.innerHTML = "";
      mic.appendChild(new Option("System default", ""));
      (devices.data.input || []).forEach((d) => mic.appendChild(new Option(d.name, d.index)));
      mic.value = s.voice.input_device == null ? "" : String(s.voice.input_device);

      $("#setWhisper").value = s.voice.whisper_model;
      $("#setWake").value = s.voice.wake_word_model;
      $("#setWakeEnabled").checked = !!s.voice.wake_word_enabled;
      $("#setStartListen").checked = !!s.startup.start_listening;
      $("#setGreeting").value = s.voice.greeting;

      $("#setEngine").value = s.speech.engine;
      $("#setVoice").value = s.speech.voice;
      $("#setRate").value = s.speech.rate;
      $("#setVolume").value = s.speech.volume;
      $("#setAccent").value = s.appearance.accent || "#00e5ff";
      $("#setAnim").checked = !!s.appearance.animations;
      $("#setScan").checked = !!s.appearance.scan_lines;

      $("#secConfirm").checked = !!s.security.confirm_destructive;
      $("#secPs").checked = !!s.security.allow_powershell;
      $("#secScreen").checked = !!s.security.allow_screen_capture;
      $("#secWrite").checked = !!s.security.allow_file_write;
      $("#secApps").checked = !!s.security.allow_app_launch;
      $("#secPower").checked = !!s.security.allow_shutdown;
      $("#secStartup").checked = !!s.startup.launch_at_login;

      const engines = devices.data.engines || {};
      $("#voiceOutput").textContent =
        `engine: ${(health.data.speech || {}).engine}\n` +
        `STT: ${((health.data.voice || {}).stt || {}).detail || "—"}\n` +
        `wake word: ${(((health.data.voice || {}).wake_word) || {}).detail || "—"}\n` +
        `piper: ${JSON.stringify(engines.engines || {})}\n` +
        `voices: ${(engines.piper_voices || []).join(", ") || "none installed"}`;

      const tbody = $("#auditTable tbody");
      tbody.innerHTML = "";
      (audit.data.entries || []).slice(0, 40).forEach((entry) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td class="dim">${(entry.created_at || "").slice(11, 19)}</td>` +
          `<td>${entry.tool}</td><td class="dim">${entry.outcome}</td>`;
        tbody.appendChild(tr);
      });
      $("#aiOutput").textContent = JSON.stringify(s.ai, null, 2);
    } catch (e) { Core().toast("Settings", e.message, "error"); }
  }

  async function saveSettings() {
    const payloads = [
      ["ai", {
        provider: $("#setProvider").value,
        model: $("#setModel").value.trim(),
        temperature: parseFloat($("#setTemp").value) || 0.4,
        context_turns: parseInt($("#setCtx").value, 10) || 12,
      }],
      ["voice", {
        input_device: $("#setMic").value === "" ? null : parseInt($("#setMic").value, 10),
        whisper_model: $("#setWhisper").value,
        wake_word_model: $("#setWake").value.trim() || "hey jarvis",
        wake_word_enabled: $("#setWakeEnabled").checked,
        greeting: $("#setGreeting").value.trim(),
      }],
      ["speech", {
        engine: $("#setEngine").value,
        voice: $("#setVoice").value.trim(),
        rate: parseFloat($("#setRate").value) || 1,
        volume: parseFloat($("#setVolume").value) || 0.9,
      }],
      ["appearance", {
        accent: $("#setAccent").value,
        animations: $("#setAnim").checked,
        scan_lines: $("#setScan").checked,
      }],
      ["security", {
        confirm_destructive: $("#secConfirm").checked,
        allow_powershell: $("#secPs").checked,
        allow_screen_capture: $("#secScreen").checked,
        allow_file_write: $("#secWrite").checked,
        allow_app_launch: $("#secApps").checked,
        allow_shutdown: $("#secPower").checked,
      }],
      ["startup", {
        start_listening: $("#setStartListen").checked,
        launch_at_login: $("#secStartup").checked,
      }],
    ];
    try {
      for (const [section, values] of payloads) {
        await put("/settings", { section, values });
      }
      if ($("#secStartup").checked) {
        await post("/settings/startup" + query({ enabled: true })).catch(() => {});
      }
      Core().setAccent($("#setAccent").value);
      Core().setAnimations($("#setAnim").checked);
      Core().setScanlines($("#setScan").checked);
      Core().toast("Settings", "Saved. Restart JARVIS to apply voice engine changes.", "info");
    } catch (e) { Core().toast("Settings", e.message, "error"); }
  }

  async function storeKey() {
    const key = $("#setKey").value.trim();
    if (!key) return;
    const provider = $("#setProvider").value;
    const envName = provider === "anthropic" ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY";
    try {
      await post(`/settings/secret${query({ name: envName, value: key })}`);
      $("#setKey").value = "";
      Core().toast("Credentials", envName + " stored securely.", "info");
    } catch (e) { Core().toast("Credentials", e.message, "error"); }
  }

  /* ==================================================================== */
  /* Page registration                                                    */
  /* ==================================================================== */
  routes.dashboard = loadDashboard;
  routes.assistant = loadAssistant;
  routes.system = loadSystem;
  routes.applications = loadApplications;
  routes.files = () => loadFiles(currentPath);
  routes.automation = loadAutomation;
  routes.memory = loadMemory;
  routes.settings = loadSettings;

  /* ==================================================================== */
  /* Control wiring                                                       */
  /* ==================================================================== */
  function wireControls() {
    const core = Core();

    // dashboard
    $("#btnSend").onclick = () => {
      const input = $("#cmdInput");
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      core.sendCommand(text);
    };
    $("#cmdInput").onkeydown = (e) => { if (e.key === "Enter") $("#btnSend").click(); };
    $("#chatSend").onclick = () => {
      const input = $("#chatInput");
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      core.sendCommand(text);
    };
    $("#chatInput").onkeydown = (e) => { if (e.key === "Enter") $("#chatSend").click(); };

    const micAction = async (btn) => {
      try {
        btn.classList.add("recording");
        await post("/voice/push-to-talk", {});
        core.toast("Voice", "Listening for your command…", "info");
        setTimeout(() => btn.classList.remove("recording"), 6000);
      } catch (e) {
        btn.classList.remove("recording");
        core.toast("Microphone", e.message, "error");
      }
    };
    $("#btnMic").onclick = (e) => micAction(e.currentTarget);
    $("#btnMic2").onclick = (e) => micAction(e.currentTarget);

    $("#btnWake").onclick = async () => {
      try {
        const status = await get("/voice/status");
        if (status.data.listening) {
          await post("/voice/stop", {});
          core.toast("Wake word", "Listening stopped.", "info");
        } else {
          await post("/voice/start?mode=wake", {});
          core.toast("Wake word", "Armed — say “Hey JARVIS”.", "info");
        }
      } catch (e) { core.toast("Wake word", e.message, "error"); }
    };
    $("#btnStopSpeak").onclick = async () => {
      await post("/voice/stop-speaking", {}).catch(() => {});
      core.toast("Voice", "Speech stopped.", "info");
    };
    $("#btnEmergency").onclick = async () => {
      try {
        await post("/emergency-stop", {});
        core.setState("idle");
        core.toast("EMERGENCY STOP", "All activity halted.", "error");
      } catch (e) { core.toast("Emergency stop", e.message, "error"); }
    };
    $("#clearLog").onclick = () => { $("#logList").innerHTML = ""; };

    // assistant
    $("#clearConv").onclick = async () => {
      await post("/conversation/clear", {});
      $("#chat").innerHTML = "";
      core.toast("Conversation", "History cleared.", "info");
    };

    // system
    document.querySelectorAll("[data-sys]").forEach((btn) => {
      btn.onclick = () => runSystemAction(btn.dataset.sys);
    });
    document.querySelectorAll("[data-power]").forEach((btn) => {
      btn.onclick = () => runPower(btn.dataset.power);
    });
    $("#psRun").onclick = runPowershell;
    $("#volSlider").onchange = async (e) => {
      const level = parseInt(e.target.value, 10);
      await post("/system/volume" + query({ level })).catch((err) =>
        core.toast("Volume", err.message, "error"));
    };
    $("#volMute").onclick = async () => {
      await post("/system/volume" + query({ mute: true })).catch((err) =>
        core.toast("Volume", err.message, "error"));
    };

    // applications
    $("#appFilter").oninput = renderApps;
    $("#launchBtn").onclick = async () => {
      const name = $("#launchInput").value.trim();
      if (!name) return;
      try {
        const res = await post("/apps/launch", { name });
        core.toast("Launch", res.message, "info");
      } catch (e) { core.toast("Launch", e.message, "error"); }
    };
    $("#launchInput").onkeydown = (e) => { if (e.key === "Enter") $("#launchBtn").click(); };

    // files
    $("#fsGo").onclick = () => loadFiles($("#fsPath").value.trim());
    $("#fsPath").onkeydown = (e) => { if (e.key === "Enter") $("#fsGo").click(); };
    $("#fsUp").onclick = () => loadFiles($("#fsPath").dataset.parent || currentPath);
    $("#fsNewFolder").onclick = async () => {
      const name = await core.promptModal("New folder name");
      if (name) fileAction("create_folder", { path: currentPath, name });
    };
    $("#fsNewFile").onclick = async () => {
      const name = await core.promptModal("New file name (e.g. notes.txt)");
      if (name) fileAction("create_file", { path: currentPath, name });
    };
    $("#fsRename").onclick = async () => {
      if (!selectedPath) return core.toast("Rename", "Select a file first.", "warning");
      const name = await core.promptModal("New name");
      if (name) fileAction("rename", { path: selectedPath, new_name: name });
    };
    $("#fsDelete").onclick = async () => {
      if (!selectedPath) return core.toast("Delete", "Select a file first.", "warning");
      fileAction("delete", { path: selectedPath });
    };
    $("#fsSearch").onclick = searchFiles;
    $("#fsQuery").onkeydown = (e) => { if (e.key === "Enter") searchFiles(); };
    $("#dupScan").onclick = async () => {
      $("#fsOutput").textContent = "Scanning for duplicates…";
      const res = await post("/files/action", { operation: "duplicates" });
      $("#fsOutput").textContent = res.message;
    };
    $("#organisePreview").onclick = async () => {
      const res = await post("/files/action",
        { operation: "organise_downloads", options: { dry_run: true } });
      $("#fsOutput").textContent = res.message;
    };

    // automation
    $("#planBtn").onclick = planGoal;
    $("#runBtn").onclick = runGoal;
    $("#cancelTasks").onclick = async () => {
      await post("/tasks/cancel", {});
      loadAutomation();
      core.toast("Tasks", "All tasks cancelled.", "warning");
    };
    $("#ccSave").onclick = async () => {
      const trigger = $("#ccTrigger").value.trim();
      let steps;
      try {
        steps = JSON.parse($("#ccSteps").value || "[]");
      } catch (e) {
        return core.toast("Custom command", "Steps must be valid JSON.", "error");
      }
      try {
        const res = await post("/automation/commands", { trigger, steps });
        core.toast("Custom command", res.message, "info");
        $("#ccTrigger").value = "";
        $("#ccSteps").value = "";
        loadAutomation();
      } catch (e) { core.toast("Custom command", e.message, "error"); }
    };

    // memory
    $("#memAdd").onclick = async () => {
      const key = $("#memKey").value.trim();
      const value = $("#memValue").value.trim();
      if (!key || !value) return;
      try {
        await post("/memory", { key, value });
        $("#memKey").value = ""; $("#memValue").value = "";
        loadMemory();
      } catch (e) { core.toast("Memory", e.message, "error"); }
    };
    $("#memClear").onclick = async () => {
      await post("/memory/clear", {});
      loadMemory();
      core.toast("Memory", "All memories cleared.", "info");
    };
    $("#memEnabled").onchange = async (e) => {
      await post("/memory/enabled" + query({ enabled: e.target.checked }));
    };

    // settings
    $("#saveSettings").onclick = saveSettings;
    $("#saveKey").onclick = storeKey;
    $("#resetSettings").onclick = async () => {
      await post("/settings/reset", {});
      loadSettings();
      core.toast("Settings", "Defaults restored.", "info");
    };
    $("#aiCheck").onclick = async () => {
      const res = await get("/ai/health");
      $("#aiOutput").textContent = JSON.stringify(res.data, null, 2);
    };
    $("#aiModels").onclick = async () => {
      const res = await get("/ai/models");
      $("#aiOutput").textContent = "Available models:\n" + (res.data.models || []).join("\n");
    };
    $("#speakTest").onclick = async () => {
      try {
        await post("/voice/speak", { text: "All systems nominal, sir. Voice output confirmed." });
      } catch (e) { core.toast("Voice", e.message, "error"); }
    };
    $("#voiceTestBtn").onclick = async () => {
      $("#voiceOutput").textContent = "Requesting a microphone test…";
      try {
        await post("/voice/push-to-talk", {});
        $("#voiceOutput").textContent = "Listening — speak now. The transcript will appear below.";
      } catch (e) {
        $("#voiceOutput").textContent = "Microphone unavailable: " + e.message;
      }
    };
    $("#setAccent").oninput = (e) => core.setAccent(e.target.value);
  }

  /* ==================================================================== */
  global.JarvisPages = { initNav, navigate, wireControls, loadDashboard,
    loadAssistant, loadSystem, loadApplications, loadFiles, loadAutomation,
    loadMemory, loadSettings, loadFiles: loadFiles, renderTasks };
})(window);
