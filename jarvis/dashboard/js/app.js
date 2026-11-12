/* J.A.R.V.I.S. — application bootstrap. */
(function (global) {
  "use strict";

  const Core = global.JarvisCore;
  const Pages = global.JarvisPages;
  const API = global.JarvisAPI;

  async function boot() {
    Core.initCore();
    Core.startClock();
    Core.runBoot();
    Pages.initNav();
    Pages.wireControls();

    const socket = new global.JarvisSocket();
    Core.wire(socket);
    socket.connect();

    // Load appearance from saved settings so the theme persists.
    try {
      const res = await API.get("/settings");
      const s = res.data || {};
      const appearance = s.appearance || {};
      Core.setAccent(appearance.accent || "#00e5ff");
      Core.setAnimations(appearance.animations !== false);
      Core.setScanlines(appearance.scan_lines !== false);
    } catch (e) { /* defaults are fine */ }

    // Task events keep the queue view live.
    Core.App.onEvent("task", (task) => {
      if (!task) return;
      const strip = document.querySelector("#taskBar");
      const text = document.querySelector("#taskText");
      if (task.status === "running") {
        if (strip) strip.style.width = Math.round((task.progress || 0) * 100) + "%";
        if (text) text.textContent = task.title + " — " + (task.detail || "");
      } else if (task.status === "done" || task.status === "failed" || task.status === "cancelled") {
        if (strip) strip.style.width = "0%";
        if (text) text.textContent = "idle";
      }
      if (Core.App.page === "automation") {
        API.get("/tasks").then((res) => Pages.renderTasks(res.data.tasks, res.data.active))
          .catch(() => {});
      }
    });

    // Live state updates from the backend
    Core.App.onEvent("response", () => { /* handled in core.wire */ });

    // Periodic status refresh (AI pill, voice status)
    Pages.loadDashboard();
    setInterval(() => Pages.loadDashboard(), 15000);

    // Refresh the active page's data when it becomes visible again.
    setInterval(() => {
      const page = Core.App.page;
      if (page === "system") Pages.loadSystem();
      if (page === "applications") Pages.loadApplications();
      if (page === "memory") Pages.loadMemory();
    }, 10000);

    // Keyboard shortcuts
    document.addEventListener("keydown", (e) => {
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "x") {
        e.preventDefault();
        document.querySelector("#btnEmergency").click();
      }
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "m") {
        e.preventDefault();
        document.querySelector("#btnMic").click();
      }
      if (e.key === "Escape") {
        document.querySelector("#permModal").classList.add("hidden");
        document.querySelector("#promptModal").classList.add("hidden");
      }
    });

    // Focus the command bar on load
    setTimeout(() => {
      const input = document.querySelector("#cmdInput");
      if (input) input.focus();
    }, 1800);
  }

  document.addEventListener("DOMContentLoaded", () => {
    boot().catch((error) => {
      console.error("Boot failed", error);
      document.body.insertAdjacentHTML("beforeend",
        `<div class="toast error" style="position:fixed;bottom:20px;right:20px;z-index:999">
           <b>Dashboard error</b><span>${error.message}</span></div>`);
    });
  });
})(window);
