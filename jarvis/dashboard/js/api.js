/* J.A.R.V.I.S. — REST + WebSocket client.
   All calls are relative so the dashboard works behind any proxy/preview host. */
(function (global) {
  "use strict";

  const BASE = "/api";

  async function request(path, options = {}) {
    const response = await fetch(BASE + path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let payload = null;
    const text = await response.text();
    try { payload = text ? JSON.parse(text) : null; } catch (e) { payload = { message: text }; }
    if (!response.ok) {
      const detail = (payload && (payload.detail || payload.message)) || response.statusText;
      const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  const get = (path) => request(path);
  const post = (path, body) => request(path, { method: "POST", body: JSON.stringify(body || {}) });
  const put = (path, body) => request(path, { method: "PUT", body: JSON.stringify(body || {}) });
  const del = (path) => request(path, { method: "DELETE" });

  function query(params) {
    const usp = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") usp.set(k, v);
    });
    const s = usp.toString();
    return s ? "?" + s : "";
  }

  /* ---------------------------------------------------------------- WS */
  const listeners = new Map();

  class Socket {
    constructor() {
      this.ws = null;
      this.retry = 0;
      this.connected = false;
    }
    connect() {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${proto}//${location.host}/ws`;
      try { this.ws = new WebSocket(url); } catch (e) { this.scheduleReconnect(); return; }

      this.ws.onopen = () => {
        this.connected = true;
        this.retry = 0;
        this.emit("_connection", { connected: true });
      };
      this.ws.onclose = () => {
        this.connected = false;
        this.emit("_connection", { connected: false });
        this.scheduleReconnect();
      };
      this.ws.onerror = () => { try { this.ws.close(); } catch (e) {} };
      this.ws.onmessage = (event) => {
        let message;
        try { message = JSON.parse(event.data); } catch (e) { return; }
        const type = message.type || "unknown";
        const data = message.data !== undefined ? message.data : message;
        if (type === "hello") {
          this.emit("hello", data);
          (data.history || []).forEach((evt) => this.emit(evt.type, evt.data, evt));
          return;
        }
        this.emit(type, data, message);
      };
    }
    scheduleReconnect() {
      this.retry = Math.min(this.retry + 1, 8);
      setTimeout(() => this.connect(), 400 * this.retry);
    }
    emit(type, data, raw) {
      (listeners.get(type) || []).forEach((fn) => {
        try { fn(data, raw); } catch (e) { console.error("listener", type, e); }
      });
      (listeners.get("*") || []).forEach((fn) => {
        try { fn(type, data); } catch (e) { console.error(e); }
      });
    }
    on(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
      return () => {
        const arr = listeners.get(type) || [];
        const i = arr.indexOf(fn);
        if (i >= 0) arr.splice(i, 1);
      };
    }
    send(payload) {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify(payload));
        return true;
      }
      return false;
    }
    command(text, speak = true) { return this.send({ action: "command", text, speak }); }
    stop() { return this.send({ action: "stop" }); }
  }

  global.JarvisAPI = { get, post, put, del, query, request };
  global.JarvisSocket = Socket;
})(window);
