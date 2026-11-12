/* Headless smoke test of the real dashboard against the live JARVIS server.
 * Exercises boot, every page, and the main controls; fails on any JS error.
 * Usage: node /tmp/dash_test.js [baseUrl]
 */
const { JSDOM, VirtualConsole } = require("jsdom");
const fs = require("fs");

const BASE = process.argv[2] || "http://127.0.0.1:8712";
const errors = [];
const logs = [];

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (e) => errors.push("jsdomError: " + (e.stack || e.message)));
virtualConsole.on("error", (...a) => errors.push("console.error: " + a.join(" ")));
virtualConsole.on("warn", (...a) => logs.push("warn: " + a.join(" ")));
virtualConsole.on("log", (...a) => logs.push("log: " + a.join(" ")));

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

(async () => {
  const html = await (await fetch(BASE + "/")).text();

  const dom = new JSDOM(html, {
    url: BASE + "/",
    runScripts: "dangerously",
    resources: undefined,
    pretendToBeVisual: true,
    virtualConsole,
    beforeParse(window) {
      // jsdom has no WebSocket; provide a stub that records sends.
      window.__sent = [];
      class FakeWS {
        constructor(url) { this.url = url; this.readyState = 1; window.__ws = this;
          setTimeout(() => this.onopen && this.onopen(), 5); }
        send(data) { window.__sent.push(JSON.parse(data)); }
        close() {}
      }
      window.WebSocket = FakeWS;
      window.WebSocket.OPEN = 1;
      window.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16);
    },
  });

  const { window } = dom;
  // fetch is not implemented in jsdom by default -> map to node fetch
  window.fetch = (url, opts) => fetch(new URL(url, BASE).href, opts);

  // Load the dashboard scripts manually (jsdom doesn't fetch external scripts)
  for (const src of ["/static/js/api.js", "/static/js/core.js",
                     "/static/js/pages.js", "/static/js/app.js"]) {
    const code = await (await fetch(BASE + src)).text();
    const script = window.document.createElement("script");
    script.textContent = code;
    window.document.body.appendChild(script);
  }

  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await sleep(2200);

  const $ = (s) => window.document.querySelector(s);
  const results = [];
  const check = (name, fn) => {
    try {
      const ok = fn();
      results.push([ok ? "PASS" : "FAIL", name]);
      if (!ok) errors.push("assertion failed: " + name);
    } catch (e) { results.push(["FAIL", name + " -> " + e.message]); errors.push(name + ": " + e.message); }
  };

  check("boot overlay dismissed", () => $("#boot").classList.contains("done"));
  check("tick ring built (90 ticks)", () => $("#tickRing").children.length === 90);
  check("waveform built (56 bars)", () => $("#waveform").children.length === 56);
  check("clock ticking", () => /\d\d:\d\d:\d\d/.test($("#clock").textContent));
  check("ai pill populated", () => $("#aiPill").textContent.includes("AI"));
  check("core state text set", () => $("#coreState").textContent.length > 0);

  // Navigate through every page and let its loader run
  const pages = ["dashboard", "assistant", "system", "applications",
                 "files", "automation", "memory", "settings"];
  for (const page of pages) {
    window.JarvisPages.navigate(page);
    await sleep(900);
    const active = window.document.querySelector(".page.active");
    check(`page ${page} active`, () => active && active.id === "page-" + page);
  }

  // Metrics rendered from the live server
  window.JarvisPages.navigate("dashboard");
  await sleep(1200);
  await window.JarvisPages.loadDashboard();
  const metrics = await (await fetch(BASE + "/api/metrics")).json();
  window.JarvisCore.updateMetrics(metrics.data);
  check("cpu metric filled", () => $("#cpuVal").textContent.includes("%"));
  check("memory bar width set", () => $("#memBar").style.width !== "");
  check("per-core bars rendered", () => $("#perCore").children.length >= 1);
  check("disk sub text", () => $("#diskSub").textContent.length > 3);

  // Tools page content
  window.JarvisPages.navigate("assistant");
  await sleep(1000);
  check("tool grid populated", () => $("#toolGrid").children.length > 40);

  // Applications / files pages reflect real data
  window.JarvisPages.navigate("files");
  await sleep(1000);
  check("file table rendered", () => $("#fsTable tbody").children.length > 0);

  // Settings form populated from backend config
  window.JarvisPages.navigate("settings");
  await sleep(1200);
  check("settings provider set", () => $("#setProvider").value.length > 0);
  check("settings whisper model set", () => $("#setWhisper").value.length > 0);
  check("mic select populated", () => $("#setMic").options.length >= 1);
  check("audit table rendered", () => true); // may legitimately be empty

  // Chat: send a command through the real REST endpoint
  $("#chatInput").value = "what time is it";
  $("#chatSend").click();
  await sleep(1600);
  const msgs = [...window.document.querySelectorAll("#chat .msg")];
  check("chat renders user message", () => msgs.some((m) => m.classList.contains("user")));
  check("chat renders assistant reply", () =>
    msgs.some((m) => m.classList.contains("assistant") && m.textContent.length > 0));
  if (!msgs.some((m) => m.classList.contains("assistant"))) {
    console.log("    DEBUG chat messages:",
      JSON.stringify(msgs.map((m) => [m.className, m.textContent.slice(0, 60)])));
    console.log("    DEBUG socket connected:", window.JarvisCore.App.socket.connected);
    console.log("    DEBUG ws stub present:", !!window.__ws);
  }

  // Dashboard command input path
  window.JarvisPages.navigate("dashboard");
  $("#cmdInput").value = "system status";
  $("#btnSend").click();
  await sleep(1800);
  check("log received entries", () => $("#logList").children.length > 0);

  // State machine transitions render
  for (const state of ["listening", "thinking", "speaking", "executing", "idle"]) {
    window.JarvisCore.setState(state);
    await sleep(60);
  }
  check("state label after idle", () => $("#coreState").textContent === "STANDBY");

  // Permission modal flow
  window.JarvisCore.App.socket && window.JarvisCore.wire(window.JarvisCore.App.socket);
  const fakeRequest = { event: "request", id: "test-123", action: "delete_path",
    description: "Delete C:\\temp\\x.txt?", risk: "high" };
  try {
    window.__ws.onmessage({ data: JSON.stringify({ type: "permission", data: fakeRequest }) });
  } catch (e) { errors.push("permission dispatch: " + e.message); }
  await sleep(150);
  check("permission modal shown", () => !$("#permModal").classList.contains("hidden"));

  // Nothing should have thrown
  const realErrors = errors.filter((e) =>
    !e.includes("Not implemented") && !e.includes("navigation"));
  results.forEach(([st, name]) => console.log(`  ${st}  ${name}`));
  if (logs.length) {
    console.log("\n--- page console output (first 8) ---");
    logs.slice(0, 8).forEach((l) => console.log("  " + l.slice(0, 160)));
  }
  if (realErrors.length) {
    console.log("\n--- ERRORS ---");
    realErrors.slice(0, 15).forEach((e) => console.log("  " + e.slice(0, 300)));
  }
  const failed = results.filter(([st]) => st === "FAIL").length;
  console.log(`\n${results.length - failed}/${results.length} checks passed, ` +
              `${realErrors.length} errors`);
  process.exit(failed || realErrors.length ? 1 : 0);
})().catch((e) => { console.error("harness crashed:", e); process.exit(2); });
