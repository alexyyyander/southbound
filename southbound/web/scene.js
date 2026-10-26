(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);

  const clientId = (() => {
    try {
      const existing = window.localStorage.getItem("southbound.scene.client");
      if (existing) return existing;
      const generated = `you-${Math.random().toString(36).slice(2, 8)}`;
      window.localStorage.setItem("southbound.scene.client", generated);
      return generated;
    } catch (_) {
      return "local";
    }
  })();

  const API = {
    state: "/api/scene/state",
    presence: "/api/scene/presence",
    automation: "/api/scene/automation",
    reset: "/api/scene/reset",
    call: "/api/northbound/call",
    intent: "/api/northbound/intent",
  };

  const ZONE_NAMES = { entry: "玄关", living: "客厅", bedroom: "卧室", study: "书房" };
  const EVENT_LABELS = {
    intent: "意图",
    "intent.result": "意图完成",
    command: "南向命令",
    state: "状态回读",
    presence: "存在信号",
    "commission.request": "接入请求",
    commission: "接入执行",
    "commission.result": "接入完成",
    decommission: "撤销接入",
    "decommission.result": "撤销完成",
    "automation.proposed": "自动建议",
    "agent.notice": "Agent 通知",
    "automation.verified": "验证通过",
    "automation.failed": "验证失败",
    "automation.mode": "自动模式",
    "scene.reset": "场景重置",
    reset: "模拟重置",
  };

  const state = {
    ready: false,
    room: null,
    placements: [],
    devices: [],
    presences: [],
    remote: new Map(),
    automation: true,
    selectedId: null,
    events: [],
    notices: [],
    pulses: [],
    eventCount: 0,
    lastEvent: null,
    agentTouched: false,
    done: { light: false, dim: false, purifier: false, agent: false },
    local: {
      x: 1.5,
      y: 6.8,
      zone: "entry",
      target: null,
      moving: false,
      facing: 0,
    },
  };

  const canvas = $("#room-canvas");
  const ctx = canvas.getContext("2d");
  const view = { scale: 1, ox: 0, oy: 0, width: 0, height: 0, dpr: 1 };
  const keys = new Set();
  const seenEventIds = new Set();
  const hits = [];
  let hoverId = null;
  let lastFrame = 0;
  let lastPresenceAt = 0;
  let noticeTimer = 0;

  const ISO_X = 32;
  const ISO_Y = 16;
  const ISO_Z = 18;
  const SPEED = 2.7;

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const payload = await response.json();
        detail = payload.error || detail;
      } catch (_) {
        /* keep status text */
      }
      throw new Error(detail);
    }
    return response.json();
  }

  function post(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  function zoneFor(x, y) {
    if (x >= 7.5) return y < 4 ? "bedroom" : "study";
    if (y >= 5.5 && x < 3) return "entry";
    return "living";
  }

  function zoneLabel(zone) {
    const match = state.room?.zones?.find((item) => item.id === zone);
    return match?.label || ZONE_NAMES[zone] || zone || "—";
  }

  function device(id) {
    return state.devices.find((item) => item.id === id);
  }

  function placement(id) {
    return state.placements.find((item) => item.device_id === id);
  }

  function deviceName(id) {
    return device(id)?.name || id || "设备";
  }

  function formatTime(value) {
    if (!value) return "";
    const text = String(value);
    const match = text.match(/T(\d{2}:\d{2}:\d{2})/);
    return match ? match[1] : text.slice(11, 19);
  }

  function formatState(value) {
    if (!value || typeof value !== "object") return String(value ?? "—");
    if ("temperature" in value) {
      return `${value.temperature}°C · 湿度 ${value.humidity}%`;
    }
    if ("open" in value) return value.open ? "打开" : "关闭";
    if ("position" in value && "moving" in value) {
      return `位置 ${value.position}% · ${value.moving ? "移动中" : "停止"}`;
    }
    if ("pm25" in value) {
      return `${value.on ? "开" : "关"} · PM2.5 ${value.pm25} · ${value.mode}`;
    }
    if ("brightness" in value) {
      return `${value.on ? "开" : "关"} · 亮度 ${value.brightness}%`;
    }
    if ("power_w" in value) {
      return `${value.on ? "开" : "关"} · ${value.power_w} W`;
    }
    return JSON.stringify(value);
  }

  function isActive(item) {
    if (!item) return false;
    const value = item.state || {};
    if (item.kind === "binary_sensor") return Boolean(value.open);
    if (item.kind === "sensor") return true;
    if (item.kind === "cover") return Boolean(value.moving) || Number(value.position) > 0;
    return Boolean(value.on);
  }

  function applySnapshot(data, resetLocal = false) {
    state.room = data.room || state.room;
    state.placements = data.placements || state.placements;
    state.devices = data.devices || state.devices;
    state.presences = data.presences || [];
    state.automation = Boolean(data.automation?.enabled);
    state.remote.clear();
    for (const presence of state.presences) {
      if (presence.client_id !== clientId) state.remote.set(presence.client_id, presence);
    }
    if (resetLocal) {
      const mine = state.presences.find((item) => item.client_id === clientId);
      const presence = mine || data.presence;
      if (presence) {
        state.local.x = Number(presence.x);
        state.local.y = Number(presence.y);
        state.local.zone = presence.zone || zoneFor(state.local.x, state.local.y);
        state.local.target = null;
      }
    }
    if (state.selectedId && !device(state.selectedId)) state.selectedId = null;
  }

  async function refreshState(initial = false) {
    const data = await api(API.state);
    applySnapshot(data, initial);
    if (initial) {
      state.ready = true;
      renderAll();
      const cutoff = await api("/api/events/recent?limit=1")
        .then((data) => {
          const events = data.events || [];
          return events.length ? events[events.length - 1].id : 0;
        })
        .catch(() => 0);
      await announce(true);
      await replayRecent(cutoff);
      return;
    }
    renderAll();
  }

  async function replayRecent(since = 0) {
    try {
      const data = await api(`/api/events/recent?since=${since}&limit=40`);
      for (const event of data.events || []) handleEvent(event);
    } catch (_) {
      /* SSE will catch subsequent events even if replay is unavailable */
    }
  }

  async function announce(force = false) {
    if (!state.ready) return;
    const now = performance.now();
    if (!force && now - lastPresenceAt < 650) return;
    lastPresenceAt = now;
    try {
      const data = await post(API.presence, {
        client_id: clientId,
        x: Number(state.local.x.toFixed(2)),
        y: Number(state.local.y.toFixed(2)),
      });
      const presence = data.presence;
      if (presence && presence.client_id === clientId) {
        const zoneChanged = presence.zone !== state.local.zone;
        state.local.zone = presence.zone || state.local.zone;
        if (zoneChanged) onZoneChanged(state.local.zone);
      }
      applySnapshot(data, false);
      renderHud();
    } catch (error) {
      showNotice("SCENE / OFFLINE", error.message, "alert", 7000);
    }
  }

  function onZoneChanged(zone) {
    renderHud();
    renderTasks();
    showNotice("PRESENCE / " + zoneLabel(zone).toUpperCase(), "你进入了" + zoneLabel(zone), "info", 2600);
  }

  function initSSE() {
    if (!window.EventSource) return;
    const source = new EventSource("/api/events");
    source.onmessage = (message) => {
      try {
        handleEvent(JSON.parse(message.data));
      } catch (_) {
        /* ignore malformed frames */
      }
    };
  }

  function handleEvent(event) {
    if (event.id != null) {
      if (seenEventIds.has(event.id)) return;
      seenEventIds.add(event.id);
      if (seenEventIds.size > 240) {
        const oldest = seenEventIds.values().next().value;
        seenEventIds.delete(oldest);
      }
    }
    state.lastEvent = event;
    const ownPresence = event.type === "presence" && event.client_id === clientId;
    if (!ownPresence) state.eventCount += 1;
    if (event.type === "presence" && !ownPresence) {
      state.remote.set(event.client_id, event);
    }
    if (event.type === "state" && event.device_id && event.state) {
      updateDeviceState(event.device_id, event.state);
    }
    if (event.type === "agent.notice") {
      addNotice(event);
      showNotice(
        "AGENT / " + String(event.severity || "notice").toUpperCase(),
        event.message || "收到场景通知",
        event.severity === "alert" ? "alert" : "info",
        5200
      );
    }
    if (event.type === "automation.verified") {
      showNotice("AUTOMATION / VERIFIED", event.title || "状态回读通过", "info", 3600);
      pulseDevice(event.device_id, "#0f7d78");
    }
    if (event.type === "automation.failed") {
      showNotice("AUTOMATION / FAILED", event.error || event.title || "执行失败", "alert", 6200);
      pulseDevice(event.device_id, "#cf4a29");
    }
    if (event.type === "automation.mode") {
      state.automation = Boolean(event.enabled);
    }
    if (event.type === "scene.reset") {
      state.remote.clear();
      window.setTimeout(() => refreshState(false).catch(() => {}), 80);
    }
    if (event.type === "commission.result" || event.type === "decommission.result") {
      window.setTimeout(() => refreshState(false).catch(() => {}), 80);
    }
    if (!ownPresence) pushEvent(event);
    renderHud();
    renderTasks();
    renderDevice();
    renderInbox();
    renderEvents();
    renderToggle();
    if (!ownPresence) pulseFlow(event);
  }

  function pushEvent(event) {
    state.events.unshift(event);
    state.events = state.events.slice(0, 12);
  }

  function addNotice(event) {
    const key = `${event.rule_id || ""}|${event.message || ""}`;
    if (
      state.notices.some(
        (notice) => `${notice.rule_id || ""}|${notice.message || ""}` === key
      )
    ) {
      return;
    }
    state.notices.unshift(event);
    state.notices = state.notices.slice(0, 4);
  }

  function updateDeviceState(id, nextState) {
    const item = device(id);
    if (!item) return;
    item.state = nextState;
    if (state.selectedId === id) renderDevice();
    renderTasks();
  }

  function renderAll() {
    renderHud();
    renderToggle();
    renderTasks();
    renderDeviceSelect();
    renderDevice();
    renderInbox();
    renderEvents();
  }

  function renderHud() {
    $("#hud-zone").textContent = state.local.zone ? zoneLabel(state.local.zone) : "—";
    $("#hud-devices").textContent = state.devices.length || "—";
    $("#hud-events").textContent = state.eventCount;
    $("#hud-automation").textContent = state.automation ? "ON" : "OFF";
  }

  function renderToggle() {
    const button = $("#automation-toggle");
    button.classList.toggle("is-on", state.automation);
    button.setAttribute("aria-pressed", String(state.automation));
    $(".toggle__value", button).textContent = state.automation ? "ON" : "OFF";
  }

  function taskDone() {
    const light = device("living_light");
    const purifier = device("bedroom_purifier");
    const plug = device("desk_plug");
    if (Boolean(light?.state?.on)) state.done.light = true;
    if (Boolean(light?.state?.on) && Number(light?.state?.brightness) <= 35) {
      state.done.dim = true;
    }
    if (Boolean(purifier?.state?.on)) state.done.purifier = true;
    if (state.agentTouched && plug && !plug.state.on) state.done.agent = true;
    return state.done;
  }

  function renderTasks() {
    const done = taskDone();
    let count = 0;
    document.querySelectorAll("#task-list li").forEach((item) => {
      const isDone = Boolean(done[item.dataset.task]);
      if (isDone) count += 1;
      item.classList.toggle("is-done", isDone);
    });
    $("#task-progress").textContent = `${count} / ${Object.keys(done).length}`;
  }

  function deviceActions(item) {
    const capabilities = new Set([...(item.capabilities || []), "read"]);
    const actions = [];
    const add = (capability, label, args = {}, active = false) => {
      if (capabilities.has(capability)) actions.push({ capability, label, args, active });
    };
    if (item.kind === "light") {
      add(item.state?.on ? "turn_off" : "turn_on", item.state?.on ? "关闭" : "打开", {}, Boolean(item.state?.on));
      add("set_brightness", "30%", { brightness: 30 }, Number(item.state?.brightness) === 30);
      add("set_brightness", "60%", { brightness: 60 }, Number(item.state?.brightness) === 60);
    } else if (item.kind === "plug") {
      add(item.state?.on ? "turn_off" : "turn_on", item.state?.on ? "关闭" : "打开", {}, Boolean(item.state?.on));
    } else if (item.kind === "purifier") {
      add(item.state?.on ? "turn_off" : "turn_on", item.state?.on ? "关闭" : "打开", {}, Boolean(item.state?.on));
      add("set_mode", "auto", { mode: "auto" }, item.state?.mode === "auto");
      add("set_mode", "sleep", { mode: "sleep" }, item.state?.mode === "sleep");
      add("set_mode", "turbo", { mode: "turbo" }, item.state?.mode === "turbo");
    } else if (item.kind === "cover") {
      add("open", "打开", {}, Number(item.state?.position) === 100);
      add("close", "关闭", {}, Number(item.state?.position) === 0);
      add("set_position", "50%", { position: 50 }, Number(item.state?.position) === 50);
      add("stop", "停止");
    } else if (item.kind === "sensor" || item.kind === "binary_sensor") {
      add("read", "读取");
      return actions;
    } else {
      add(item.state?.on ? "turn_off" : "turn_on", item.state?.on ? "关闭" : "打开", {}, Boolean(item.state?.on));
    }
    add("read", "读取");
    return actions;
  }

  function renderDeviceSelect() {
    const select = $("#device-select");
    const previous = state.selectedId || "";
    const options = state.devices
      .map(
        (item) =>
          `<option value="${esc(item.id)}">${esc(item.name)} · ${esc(item.room || "—")} · ${esc(item.tier || "—")}</option>`
      )
      .join("");
    select.innerHTML = `<option value="">选择设备…</option>${options}`;
    select.value = state.devices.some((item) => item.id === previous) ? previous : "";
    if (!select.value) state.selectedId = null;
  }

  function renderDevice() {
    const card = $("#device-card");
    const selected = device(state.selectedId);
    if (!selected) {
      $("#device-mode").textContent = "点击选择";
      card.innerHTML = '<p class="empty">点击房间里的设备，或从上方列表选择。</p>';
      return;
    }
    const spot = placement(selected.id);
    $("#device-mode").textContent = spot ? "场景内" : "未放置";
    const actions = deviceActions(selected);
    const actionMarkup = actions
      .map(
        (action) =>
          `<button type="button" data-device="${esc(selected.id)}" data-cap="${esc(action.capability)}" data-args="${esc(JSON.stringify(action.args))}" class="${action.active ? "is-active" : ""}">${esc(action.label)}</button>`
      )
      .join("");
    card.innerHTML = `
      <div class="device-card__head">
        <div>
          <strong>${esc(selected.name)}</strong>
          <p class="device-card__meta">${esc(selected.room || "—")} · ${esc(selected.protocol || "—")} · ${esc(selected.tier || "—")} · ${esc(selected.kind || "—")}</p>
        </div>
        <span class="status-dot ${isActive(selected) ? "is-on" : ""}"></span>
      </div>
      <p class="device-state">${esc(formatState(selected.state))}</p>
      <div class="device-actions">${actionMarkup}</div>
    `;
  }

  function renderInbox() {
    const inbox = $("#agent-inbox");
    if (!state.notices.length) {
      inbox.innerHTML = '<p class="empty">等待 agent.notice…</p>';
      return;
    }
    inbox.innerHTML = state.notices
      .map((notice) => {
        const cls = notice.severity === "alert" ? "inbox-item is-alert" : "inbox-item";
        const requires = notice.requires ? ` · ${notice.requires}` : "";
        return `<article class="${cls}"><p>${esc(notice.message || "场景通知")}</p><span>${esc(notice.rule_id || "agent.notice")}${esc(requires)}</span></article>`;
      })
      .join("");
  }

  function eventText(event) {
    const name = event.name || deviceName(event.device_id);
    switch (event.type) {
      case "intent":
        return `“${event.goal || ""}”`;
      case "intent.result":
        return event.summary || "意图完成";
      case "command":
        return `${name} · ${event.capability || "command"}${event.ok === false ? " · 失败" : ""}`;
      case "state":
        return `${name} · ${formatState(event.state)}`;
      case "presence":
        return `${event.zone_label || zoneLabel(event.zone)} · ${event.client_id === clientId ? "你" : event.client_id || "访客"}`;
      case "automation.proposed":
        return `${event.title || "自动建议"} → ${event.action?.device_id || ""}.${event.action?.capability || ""}`;
      case "agent.notice":
        return event.message || "Agent 通知";
      case "automation.verified":
        return `${event.title || "自动动作"} · 已验证`;
      case "automation.failed":
        return `${event.title || "自动动作"} · ${event.error || "失败"}`;
      case "automation.mode":
        return `自动模式 ${event.enabled ? "开启" : "关闭"}`;
      case "commission.result":
        return `${event.name || name} · 接入完成`;
      case "decommission.result":
        return `${event.name || name} · 已撤销接入`;
      case "scene.reset":
        return "房间回到玄关";
      default:
        return event.error || event.type;
    }
  }

  function eventTone(event) {
    if (event.ok === false) return "failed";
    if (String(event.type).startsWith("automation")) return "automation";
    if (event.type === "presence") return "presence";
    return "signal";
  }

  function renderEvents() {
    const list = $("#event-list");
    if (!state.events.length) {
      list.innerHTML = '<li class="empty">等待第一个事件…</li>';
      return;
    }
    list.innerHTML = state.events
      .map((event) => {
        const meta = `${event.plane || "—"} / ${event.direction || "—"} · ${formatTime(event.ts)}`;
        return `<li class="event event--${eventTone(event)}"><span class="event__dot"></span><div><p><strong>${esc(EVENT_LABELS[event.type] || event.type)}</strong> · ${esc(eventText(event))}</p><span>${esc(meta)}</span></div></li>`;
      })
      .join("");
  }

  function lastEventText(event) {
    if (!event) return "等待第一个事件";
    return `${EVENT_LABELS[event.type] || event.type} · ${eventText(event)}`;
  }

  function pulseFlow(event) {
    const line = event.plane === "northbound" ? $("#flow-north") : $("#flow-south");
    if (!line) return;
    line.classList.remove("is-active", "is-reverse");
    if (event.direction === "up" || event.direction === "out") line.classList.add("is-reverse");
    void line.offsetWidth;
    line.classList.add("is-active");
    window.setTimeout(() => line.classList.remove("is-active", "is-reverse"), 800);
  }

  function pulseDevice(id, color) {
    const spot = placement(id);
    if (!spot) return;
    state.pulses.push({
      x: spot.x,
      y: spot.y,
      z: spot.z || 0,
      start: performance.now(),
      color,
    });
  }

  function showNotice(kicker, message, tone = "info", duration = 4200) {
    const notice = $("#scene-notice");
    notice.innerHTML = `<span>${esc(kicker)}</span>${esc(message)}`;
    notice.classList.toggle("is-alert", tone === "alert");
    notice.classList.add("is-visible");
    window.clearTimeout(noticeTimer);
    noticeTimer = window.setTimeout(() => notice.classList.remove("is-visible"), duration);
  }

  function renderSuggestions() {
    const goals = ["关闭书房插座", "把客厅灯调到 30%", "家里状态"];
    $("#agent-suggestions").innerHTML = goals
      .map((goal) => `<button class="suggestion" type="button" data-goal="${esc(goal)}">${esc(goal)}</button>`)
      .join("");
  }

  async function runGoal(goal) {
    const value = String(goal || "").trim();
    if (!value) return;
    const button = $("#agent-form button");
    button.disabled = true;
    button.textContent = "执行中";
    $("#agent-summary").innerHTML = '<p>正在规划并调用工具…</p>';
    try {
      const result = await post(API.intent, { goal: value });
      if (/插座|plug/i.test(value)) state.agentTouched = true;
      const steps = result.steps || [];
      $("#agent-summary").innerHTML = `<p class="${result.ok ? "" : "is-error"}">${esc(result.summary || "完成")}</p><p class="agent-steps">${steps.length} 个步骤 · northbound intent</p>`;
      showNotice("AGENT / INTENT", result.summary || "意图完成", result.ok ? "info" : "alert", 4600);
      await refreshState(false);
    } catch (error) {
      $("#agent-summary").innerHTML = `<p class="is-error">执行失败：${esc(error.message)}</p>`;
      showNotice("AGENT / ERROR", error.message, "alert", 6200);
    } finally {
      button.disabled = false;
      button.textContent = "执行";
    }
  }

  async function callDevice(deviceId, capability, args = {}) {
    const item = device(deviceId);
    if (!item) return;
    try {
      const result = await post(API.call, {
        device_id: deviceId,
        capability,
        arguments: args,
        confirm: false,
      });
      const nextState = result.result?.state;
      if (nextState) updateDeviceState(deviceId, nextState);
      showNotice("SOUTHBOUND / CALL", `${item.name} · ${capability} 完成`, "info", 2800);
      await refreshState(false);
    } catch (error) {
      showNotice("POLICY / ADAPTER", error.message, "alert", 6200);
    }
  }

  async function toggleAutomation() {
    try {
      const data = await post(API.automation, { enabled: !state.automation });
      applySnapshot(data, false);
      renderAll();
      showNotice("AUTOMATION / MODE", state.automation ? "自动模式已开启" : "自动模式已关闭", "info", 2600);
    } catch (error) {
      showNotice("AUTOMATION / ERROR", error.message, "alert", 5200);
    }
  }

  async function resetScene() {
    try {
      const data = await post(API.reset, {});
      state.events = [];
      state.notices = [];
      state.pulses = [];
      state.eventCount = 0;
      state.agentTouched = false;
      state.done = { light: false, dim: false, purifier: false, agent: false };
      state.selectedId = null;
      applySnapshot(data, true);
      renderAll();
      showNotice("SCENE / RESET", "房间回到玄关，模拟设备已重置", "info", 3000);
    } catch (error) {
      showNotice("SCENE / ERROR", error.message, "alert", 5200);
    }
  }

  function bindUi() {
    $("#automation-toggle").addEventListener("click", toggleAutomation);
    $("#scene-reset").addEventListener("click", resetScene);

    $("#device-select").addEventListener("change", (event) => {
      state.selectedId = event.target.value || null;
      renderDevice();
    });

    $("#device-card").addEventListener("click", (event) => {
      const button = event.target.closest("[data-cap]");
      if (!button) return;
      let args = {};
      try {
        args = button.dataset.args ? JSON.parse(button.dataset.args) : {};
      } catch (_) {
        args = {};
      }
      callDevice(button.dataset.device, button.dataset.cap, args);
    });

    $("#agent-form").addEventListener("submit", (event) => {
      event.preventDefault();
      runGoal($("#agent-input").value);
    });

    $("#agent-suggestions").addEventListener("click", (event) => {
      const button = event.target.closest("[data-goal]");
      if (!button) return;
      $("#agent-input").value = button.dataset.goal;
      runGoal(button.dataset.goal);
    });

    window.addEventListener("keydown", (event) => {
      if (event.target.closest && event.target.closest("input, textarea, select, button")) return;
      const key = event.key.toLowerCase();
      if (!["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(key)) return;
      event.preventDefault();
      keys.add(key);
    });
    window.addEventListener("keyup", (event) => {
      keys.delete(event.key.toLowerCase());
    });
    window.addEventListener("blur", () => keys.clear());

    canvas.addEventListener("pointermove", (event) => {
      const point = canvasPoint(event);
      const hit = hitAt(point.x, point.y);
      hoverId = hit?.id || null;
      canvas.style.cursor = hit ? "pointer" : "crosshair";
    });
    canvas.addEventListener("pointerleave", () => {
      hoverId = null;
      canvas.style.cursor = "crosshair";
    });
    canvas.addEventListener("click", (event) => {
      const point = canvasPoint(event);
      const hit = hitAt(point.x, point.y);
      if (hit) {
        state.selectedId = hit.id;
        renderDeviceSelect();
        renderDevice();
        return;
      }
      const floor = floorPoint(point.x, point.y);
      if (!floor) return;
      state.local.target = { x: floor.x, y: floor.y };
    });

    window.addEventListener("resize", resize);
    if (window.ResizeObserver) {
      new ResizeObserver(resize).observe($(".stage__canvas"));
    }
  }

  function canvasPoint(event) {
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left - view.ox) / view.scale;
    const y = (event.clientY - rect.top - view.oy) / view.scale;
    return { x, y };
  }

  function floorPoint(x, y) {
    if (!state.room) return null;
    const u = x / ISO_X;
    const v = y / ISO_Y;
    const wx = (u + v) / 2;
    const wy = (v - u) / 2;
    if (wx < 0 || wx > state.room.width || wy < 0 || wy > state.room.depth) return null;
    return { x: wx, y: wy };
  }

  function hitAt(x, y) {
    let nearest = null;
    let distance = Infinity;
    for (const hit of hits) {
      const d = Math.hypot(hit.x - x, hit.y - y);
      if (d <= hit.r && d < distance) {
        nearest = hit;
        distance = d;
      }
    }
    return nearest;
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.round(rect.width * dpr);
    const height = Math.round(rect.height * dpr);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    view.width = rect.width;
    view.height = rect.height;
    view.dpr = dpr;
    view.scale = Math.min(rect.width / 760, rect.height / 500);
    view.ox = rect.width / 2 - 64 * view.scale;
    view.oy = rect.height / 2 - 131 * view.scale;
  }

  function world(x, y, z = 0) {
    return [(x - y) * ISO_X, (x + y) * ISO_Y - z * ISO_Z];
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function step(dt) {
    if (!state.room) return;
    let dx = 0;
    let dy = 0;
    if (keys.has("w") || keys.has("arrowup")) dy -= 1;
    if (keys.has("s") || keys.has("arrowdown")) dy += 1;
    if (keys.has("a") || keys.has("arrowleft")) dx -= 1;
    if (keys.has("d") || keys.has("arrowright")) dx += 1;

    let moving = false;
    if (dx || dy) {
      state.local.target = null;
      const length = Math.hypot(dx, dy) || 1;
      state.local.x += (dx / length) * SPEED * dt;
      state.local.y += (dy / length) * SPEED * dt;
      state.local.facing = Math.atan2(dy, dx);
      moving = true;
    } else if (state.local.target) {
      const tx = state.local.target.x;
      const ty = state.local.target.y;
      const ox = tx - state.local.x;
      const oy = ty - state.local.y;
      const distance = Math.hypot(ox, oy);
      if (distance <= 0.06) {
        state.local.target = null;
      } else {
        const stepSize = Math.min(SPEED * dt, distance);
        state.local.x += (ox / distance) * stepSize;
        state.local.y += (oy / distance) * stepSize;
        state.local.facing = Math.atan2(oy, ox);
        moving = true;
      }
    }

    state.local.x = clamp(state.local.x, 0.25, state.room.width - 0.25);
    state.local.y = clamp(state.local.y, 0.25, state.room.depth - 0.25);
    state.local.moving = moving;
    const zone = zoneFor(state.local.x, state.local.y);
    if (zone !== state.local.zone) {
      state.local.zone = zone;
      renderHud();
      renderTasks();
      announce(true);
    } else if (moving) {
      announce(false);
    }

    for (const [id, presence] of state.remote) {
      const age = Date.now() - Date.parse(presence.updated || 0);
      if (Number.isFinite(age) && age > 10000) state.remote.delete(id);
    }
  }

  function loop(timestamp) {
    if (!lastFrame) lastFrame = timestamp;
    const dt = Math.min((timestamp - lastFrame) / 1000, 0.05);
    lastFrame = timestamp;
    step(dt);
    draw(timestamp);
    window.requestAnimationFrame(loop);
  }

  function poly(points, fill, stroke, width = 1) {
    if (!points.length) return;
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i += 1) ctx.lineTo(points[i][0], points[i][1]);
    ctx.closePath();
    if (fill) {
      ctx.fillStyle = fill;
      ctx.fill();
    }
    if (stroke) {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = width;
      ctx.stroke();
    }
  }

  function line(from, to, stroke, width = 1, dash = []) {
    ctx.save();
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(from[0], from[1]);
    ctx.lineTo(to[0], to[1]);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = width;
    ctx.stroke();
    ctx.restore();
  }

  function isoBox(x, y, w, d, h, top, side, stroke = "#12262f", lineWidth = 0.7) {
    const A = world(x, y, h);
    const B = world(x + w, y, h);
    const C = world(x + w, y + d, h);
    const D = world(x, y + d, h);
    const B0 = world(x + w, y, 0);
    const C0 = world(x + w, y + d, 0);
    const D0 = world(x, y + d, 0);
    poly([A, B, C, D], top, stroke, lineWidth);
    poly([B, C, C0, B0], side, stroke, lineWidth);
    poly([D, C, C0, D0], side, stroke, lineWidth);
  }

  function drawFloor() {
    const room = state.room;
    const W = room.width;
    const D = room.depth;
    poly([world(0, 0), world(W, 0), world(W, D), world(0, D)], "#e7eeed", "#12262f", 1.1);
    for (let x = 1; x < W; x += 1) {
      line(world(x, 0), world(x, D), "rgba(18,38,47,0.075)", 0.5);
    }
    for (let y = 1; y < D; y += 1) {
      line(world(0, y), world(W, y), "rgba(18,38,47,0.075)", 0.5);
    }
    poly([world(1.5, 1.4), world(4.4, 1.0), world(4.4, 4.0), world(1.5, 4.4)], "rgba(15,125,120,0.10)", null);
    poly([world(8.3, 0.7), world(11.3, 0.7), world(11.3, 3.4), world(8.3, 3.4)], "rgba(207,74,41,0.08)", null);
    poly([world(8.3, 4.5), world(11.3, 4.5), world(11.3, 7.3), world(8.3, 7.3)], "rgba(163,111,24,0.08)", null);
    line(world(3, 5.5), world(7.5, 5.5), "rgba(18,38,47,0.25)", 0.6, [3, 4]);
    line(world(7.5, 0), world(7.5, 8), "rgba(18,38,47,0.25)", 0.6, [3, 4]);
  }

  function drawGlow() {
    const light = device("living_light");
    if (light?.state?.on) {
      const [x, y] = world(3.4, 2.0, 0);
      const gradient = ctx.createRadialGradient(x, y, 5, x, y, 135);
      gradient.addColorStop(0, "rgba(242,201,107,0.30)");
      gradient.addColorStop(1, "rgba(242,201,107,0)");
      ctx.save();
      ctx.fillStyle = gradient;
      ctx.beginPath();
      ctx.ellipse(x, y, 135, 76, -0.46, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
    const purifier = device("bedroom_purifier");
    if (purifier?.state?.on) {
      const [x, y] = world(10.15, 2.95, 0);
      const gradient = ctx.createRadialGradient(x, y, 2, x, y, 76);
      gradient.addColorStop(0, "rgba(15,125,120,0.24)");
      gradient.addColorStop(1, "rgba(15,125,120,0)");
      ctx.save();
      ctx.fillStyle = gradient;
      ctx.beginPath();
      ctx.ellipse(x, y, 76, 42, -0.46, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }

  function drawWalls() {
    const room = state.room;
    const W = room.width;
    const H = room.wall_height;
    poly(
      [world(0, 0, 0), world(W, 0, 0), world(W, 0, H), world(0, 0, H)],
      "#edf3f2",
      "#12262f",
      1.1
    );
    poly(
      [world(0, 0, 0), world(0, room.depth, 0), world(0, room.depth, H), world(0, 0, H)],
      "#e2ebe9",
      "#12262f",
      1.1
    );
    for (let x = 3; x < W; x += 3) {
      line(world(x, 0, 0), world(x, 0, H), "rgba(18,38,47,0.08)", 0.5);
    }
    for (let z = 1; z < H; z += 1) {
      line(world(0, 0, z), world(W, 0, z), "rgba(18,38,47,0.07)", 0.5);
      line(world(0, 0, z), world(0, room.depth, z), "rgba(18,38,47,0.07)", 0.5);
    }
    // Window on the back wall.
    poly(
      [world(4.6, 0, 1.15), world(7.1, 0, 1.15), world(7.1, 0, 2.55), world(4.6, 0, 2.55)],
      "#cfe3e1",
      "#12262f",
      0.8
    );
    line(world(5.85, 0, 1.15), world(5.85, 0, 2.55), "rgba(18,38,47,0.35)", 0.6);
    line(world(4.6, 0, 1.85), world(7.1, 0, 1.85), "rgba(18,38,47,0.35)", 0.6);
    poly(
      [world(8.8, 0, 1.25), world(10.8, 0, 1.25), world(10.8, 0, 2.15), world(8.8, 0, 2.15)],
      "#b8894f",
      "#12262f",
      0.8
    );
    line(world(9.15, 0, 1.5), world(10.45, 0, 1.9), "rgba(232,237,238,0.65)", 0.7);
    line(world(9.15, 0, 1.9), world(10.45, 0, 1.5), "rgba(232,237,238,0.45)", 0.5);
    // Entry door on the left wall.
    const door = device("entry_door");
    const doorFill = door?.state?.open ? "#b85a3c" : "#d9e4e2";
    poly(
      [world(0, 6.55, 0), world(0, 7.9, 0), world(0, 7.9, 2.2), world(0, 6.55, 2.2)],
      doorFill,
      "#12262f",
      0.9
    );
    line(world(0, 6.55, 2.2), world(0, 7.9, 2.2), "#12262f", 1.4);
    ctx.save();
    ctx.fillStyle = "#12262f";
    const handle = world(0, 7.6, 1.05);
    ctx.beginPath();
    ctx.arc(handle[0], handle[1], 1.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function drawZoneLabels() {
    if (view.scale < 0.68) return;
    ctx.save();
    ctx.textAlign = "center";
    ctx.fillStyle = "rgba(18,38,47,0.34)";
    ctx.font = "12px 'Iowan Old Style', Palatino, serif";
    for (const zone of state.room.zones) {
      const [x, y] = world(zone.x, zone.y, 0);
      ctx.fillText(zone.label, x, y + 5);
    }
    ctx.restore();
  }

  function drawFurniture() {
    const items = [];
    items.push({ depth: 3.1, draw: () => drawSofa() });
    items.push({ depth: 5.6, draw: () => drawDining() });
    items.push({ depth: 6.2, draw: () => drawCoffeeTable() });
    items.push({ depth: 9.7, draw: () => drawBed() });
    items.push({ depth: 12.4, draw: () => drawShelf() });
    items.push({ depth: 13.2, draw: () => drawDesk() });
    items.push({ depth: 5.9, draw: () => drawPlant() });
    return items;
  }

  function drawShadow(x, y, w, d) {
    poly(
      [world(x + 0.08, y + 0.08), world(x + w + 0.08, y + 0.08), world(x + w + 0.08, y + d + 0.08), world(x + 0.08, y + d + 0.08)],
      "rgba(18,38,47,0.09)",
      null
    );
  }

  function drawSofa() {
    drawShadow(1.5, 1.5, 2.4, 1.1);
    isoBox(1.5, 1.5, 2.4, 1.1, 0.55, "#4f6a72", "#3e555d");
    isoBox(1.5, 1.5, 2.4, 0.22, 1.05, "#526e76", "#435a63");
    isoBox(1.5, 2.4, 0.25, 0.2, 0.72, "#435a63", "#354a52");
    isoBox(3.65, 2.4, 0.25, 0.2, 0.72, "#435a63", "#354a52");
    ctx.save();
    ctx.strokeStyle = "rgba(232,237,238,0.45)";
    ctx.lineWidth = 0.6;
    const cushion = [world(1.85, 1.78, 0.56), world(2.5, 1.78, 0.56), world(2.5, 2.42, 0.56)];
    ctx.beginPath();
    ctx.moveTo(cushion[0][0], cushion[0][1]);
    ctx.lineTo(cushion[1][0], cushion[1][1]);
    ctx.lineTo(cushion[2][0], cushion[2][1]);
    ctx.stroke();
    ctx.restore();
  }

  function drawDining() {
    drawShadow(1.2, 4.9, 1.5, 0.9);
    isoBox(1.2, 4.9, 1.5, 0.9, 0.58, "#a97c48", "#8b6438");
    isoBox(0.62, 4.62, 0.45, 0.45, 0.45, "#526e76", "#435a63");
    isoBox(2.5, 5.28, 0.45, 0.45, 0.45, "#526e76", "#435a63");
    const [x, y] = world(1.95, 5.35, 0.61);
    ctx.save();
    ctx.fillStyle = "#e8edee";
    ctx.strokeStyle = "#12262f";
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    ctx.arc(x, y, 3.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  function drawCoffeeTable() {
    drawShadow(3.9, 2.25, 1.15, 0.8);
    isoBox(3.9, 2.25, 1.15, 0.8, 0.34, "#b8894f", "#9b6d37");
    poly(
      [world(4.0, 2.32, 0.36), world(4.4, 2.32, 0.36), world(4.4, 2.55, 0.36), world(4.0, 2.55, 0.36)],
      "#e8edee",
      "#12262f",
      0.5
    );
  }

  function drawBed() {
    drawShadow(8.45, 0.75, 2.55, 1.75);
    isoBox(8.45, 0.75, 2.55, 1.75, 0.52, "#607881", "#4d646d");
    poly(
      [world(8.7, 0.95, 0.53), world(10.0, 0.95, 0.53), world(10.0, 1.35, 0.53), world(8.7, 1.35, 0.53)],
      "#e5eceb",
      "#12262f",
      0.5
    );
    isoBox(8.45, 0.75, 2.55, 0.2, 0.95, "#526a73", "#445961");
    drawShadow(11.2, 0.72, 0.5, 0.62);
    isoBox(11.2, 0.72, 0.5, 0.62, 0.48, "#a97c48", "#8b6438");
  }

  function drawShelf() {
    drawShadow(11.22, 4.65, 0.52, 1.85);
    isoBox(11.22, 4.65, 0.52, 1.85, 1.8, "#526a73", "#435a63");
    for (let z = 0.45; z < 1.7; z += 0.42) {
      line(world(11.2, 4.7, z), world(11.2, 6.42, z), "rgba(232,237,238,0.45)", 0.55);
    }
  }

  function drawDesk() {
    drawShadow(8.25, 4.75, 2.4, 1.0);
    isoBox(8.25, 4.75, 2.4, 1.0, 0.72, "#b8894f", "#9b6d37");
    isoBox(8.6, 4.9, 1.2, 0.65, 0.28, "#435a63", "#354a52");
    line(world(9.15, 4.88, 0.76), world(9.15, 4.88, 1.3), "#12262f", 0.8);
    line(world(9.0, 4.88, 1.3), world(9.3, 4.88, 1.3), "#12262f", 0.8);
  }

  function drawPlant() {
    isoBox(0.68, 4.7, 0.55, 0.55, 0.58, "#8d5e43", "#714633");
    const [x, y] = world(0.95, 4.97, 0.62);
    ctx.save();
    ctx.fillStyle = "#3f7d6d";
    for (const [dx, dy, r] of [
      [-4, -9, 5],
      [5, -7, 4],
      [0, -14, 5],
      [-8, -2, 3],
      [8, 0, 3],
    ]) {
      ctx.beginPath();
      ctx.arc(x + dx, y + dy, r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  function drawTag(id, label, tier, x, y, active) {
    if (view.scale < 0.78 && state.selectedId !== id && hoverId !== id) return;
    const text = `${tier}  ${label}`;
    ctx.save();
    ctx.font = "9px 'SF Mono', Menlo, monospace";
    const width = ctx.measureText(text).width + 9;
    const bx = x + 11;
    const by = y - 14;
    line([x + 2, y - 4], [bx - 2, by], "rgba(18,38,47,0.45)", 0.6);
    ctx.fillStyle = state.selectedId === id ? "#cf4a29" : "rgba(18,38,47,0.88)";
    ctx.fillRect(bx, by - 9, width, 13);
    ctx.fillStyle = state.selectedId === id || active ? "#e8edee" : "#c4d1d2";
    ctx.fillText(text, bx + 4.5, by + 0.5);
    ctx.restore();
  }

  function drawPendant(place, item) {
    const [bx, by] = world(place.x, place.y, place.z);
    const [tx, ty] = world(place.x, place.y, state.room.wall_height);
    line([tx, ty], [bx, by], "rgba(18,38,47,0.65)", 0.8);
    ctx.save();
    ctx.fillStyle = item?.state?.on ? "#f2c96b" : "#8c9a9d";
    ctx.strokeStyle = "#12262f";
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    ctx.moveTo(bx - 11, by - 1);
    ctx.lineTo(bx + 11, by - 1);
    ctx.lineTo(bx + 6, by + 7);
    ctx.lineTo(bx - 6, by + 7);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(bx, by + 11, 4.5, 0, Math.PI * 2);
    ctx.fillStyle = item?.state?.on ? "#fff2c4" : "#a7b2b3";
    ctx.fill();
    ctx.stroke();
    ctx.restore();
    hits.push({ id: item.id, x: bx, y: by + 8, r: 24 });
    drawTag(item.id, item.name, item.tier, bx, by, Boolean(item?.state?.on));
  }

  function drawCurtain(place, item) {
    const position = Number(item?.state?.position ?? 0);
    const open = position / 100;
    const y0 = place.y - 0.55;
    const y1 = place.y + 0.55;
    const x = 0.06;
    ctx.save();
    ctx.globalAlpha = 0.5 + (1 - open) * 0.5;
    poly(
      [world(x, y0, 0.25), world(x, y1, 0.25), world(x, y1, 2.55), world(x, y0, 2.55)],
      "#b8894f",
      "#12262f",
      0.7
    );
    for (let i = 1; i < 4; i += 1) {
      const yy = y0 + ((y1 - y0) * i) / 4;
      line(world(x, yy, 0.28), world(x, yy, 2.52), "rgba(18,38,47,0.22)", 0.45);
    }
    ctx.restore();
    const [ax, ay] = world(x, place.y, place.z);
    hits.push({ id: item.id, x: ax, y: ay, r: 22 });
    drawTag(item.id, item.name, item.tier, ax, ay, position > 0);
  }

  function drawWallBox(place, item) {
    const [x, y] = world(place.x, place.y, place.z);
    ctx.save();
    ctx.fillStyle = item?.state?.open ? "#cf4a29" : "#e8edee";
    ctx.strokeStyle = "#12262f";
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    ctx.arc(x, y, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = item?.state?.open ? "#e8edee" : "#12262f";
    ctx.beginPath();
    ctx.arc(x, y, 2, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    hits.push({ id: item.id, x, y, r: 19 });
    drawTag(item.id, item.name, item.tier, x, y, Boolean(item?.state?.open));
  }

  function drawDoorDevice(place, item) {
    const open = Boolean(item?.state?.open);
    const [x, y] = world(0.03, place.y, 1.0);
    ctx.save();
    ctx.strokeStyle = open ? "#cf4a29" : "#12262f";
    ctx.fillStyle = open ? "#cf4a29" : "#e8edee";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(x, y, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = open ? "#e8edee" : "#12262f";
    ctx.fillRect(x - 3, y - 1, 6, 2);
    ctx.restore();
    hits.push({ id: item.id, x, y, r: 19 });
    drawTag(item.id, item.name, item.tier, x, y, open);
  }

  function drawPlug(place, item) {
    const [x, y] = world(place.x, place.y, place.z);
    ctx.save();
    ctx.fillStyle = item?.state?.on ? "#0f7d78" : "#e8edee";
    ctx.strokeStyle = "#12262f";
    ctx.lineWidth = 0.7;
    ctx.fillRect(x - 7, y - 3, 14, 7);
    ctx.strokeRect(x - 7, y - 3, 14, 7);
    ctx.fillStyle = "#12262f";
    ctx.fillRect(x - 3, y - 1, 1, 6);
    ctx.fillRect(x + 2, y - 1, 1, 6);
    ctx.restore();
    hits.push({ id: item.id, x, y, r: 18 });
    drawTag(item.id, item.name, item.tier, x, y, Boolean(item?.state?.on));
  }

  function drawPurifier(place, item) {
    const [x, y] = world(place.x, place.y, 0);
    isoBox(place.x - 0.34, place.y - 0.34, 0.68, 0.68, 1.08, "#e8edee", "#cbd8d7");
    ctx.save();
    ctx.strokeStyle = item?.state?.on ? "#0f7d78" : "#12262f";
    ctx.fillStyle = item?.state?.on ? "#0f7d78" : "#e8edee";
    ctx.lineWidth = 0.9;
    const [cx, cy] = world(place.x, place.y, 0.68);
    ctx.beginPath();
    ctx.arc(cx, cy, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = item?.state?.on ? "#e8edee" : "#12262f";
    ctx.beginPath();
    ctx.arc(cx, cy, 2.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    hits.push({ id: item.id, x: cx, y: cy, r: 21 });
    drawTag(item.id, item.name, item.tier, cx, cy, Boolean(item?.state?.on));
  }

  function drawDevice(place, item) {
    const selected = state.selectedId === item.id;
    if (hoverId === item.id || selected) {
      const [x, y] = world(place.x, place.y, 0);
      ctx.save();
      ctx.strokeStyle = selected ? "rgba(207,74,41,0.55)" : "rgba(15,125,120,0.45)";
      ctx.lineWidth = 0.9;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.ellipse(x, y, 18, 9, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
    switch (item.kind) {
      case "light":
        drawPendant(place, item);
        break;
      case "cover":
        drawCurtain(place, item);
        break;
      case "binary_sensor":
        if (item.id === "entry_door") drawDoorDevice(place, item);
        else drawWallBox(place, item);
        break;
      case "sensor":
        drawWallBox(place, item);
        break;
      case "plug":
        drawPlug(place, item);
        break;
      case "purifier":
        drawPurifier(place, item);
        break;
      default:
        drawWallBox(place, item);
    }
  }

  function roundedRectPath(x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + width - r, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + r);
    ctx.lineTo(x + width, y + height - r);
    ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
    ctx.lineTo(x + r, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  function drawAvatar(presence, options = {}, now = 0) {
    const [x, y] = world(presence.x, presence.y, 0);
    const alpha = options.remote ? 0.48 : 1;
    const color = options.remote ? "#0f7d78" : "#cf4a29";
    const head = options.remote ? "#c8e0dd" : "#f2e7d5";
    const gait = options.moving ? Math.sin(now / 90) * 3.2 : 0;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.fillStyle = "rgba(18,38,47,0.18)";
    ctx.beginPath();
    ctx.ellipse(x, y + 1, 11, 4, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#12262f";
    ctx.lineWidth = 1.4;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(x - 4 + gait, y - 15);
    ctx.lineTo(x - 4, y - 1);
    ctx.moveTo(x + 4 - gait, y - 15);
    ctx.lineTo(x + 4, y - 1);
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.strokeStyle = "#12262f";
    ctx.lineWidth = 0.9;
    roundedRectPath(x - 6, y - 30, 12, 17, 3);
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x, y - 36, 6.5, 0, Math.PI * 2);
    ctx.fillStyle = head;
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#12262f";
    ctx.beginPath();
    ctx.arc(x - 1.5, y - 37.5, 0.9, 0, Math.PI * 2);
    ctx.arc(x + 2.5, y - 37.5, 0.9, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = color;
    ctx.fillRect(x - 7, y - 28, 14, 2.5);
    if (view.scale > 0.7 || options.label) {
      ctx.font = "8px 'SF Mono', Menlo, monospace";
      ctx.fillStyle = options.remote ? "#0f7d78" : "#cf4a29";
      ctx.textAlign = "center";
      ctx.fillText(options.remote ? String(presence.client_id).slice(0, 10) : "YOU", x, y - 46);
    }
    ctx.restore();
  }

  function drawTarget() {
    if (!state.local.target) return;
    const [x, y] = world(state.local.target.x, state.local.target.y, 0);
    const [lx, ly] = world(state.local.x, state.local.y, 0);
    line([lx, ly], [x, y], "rgba(207,74,41,0.28)", 0.7, [2, 4]);
    ctx.save();
    ctx.strokeStyle = "rgba(207,74,41,0.65)";
    ctx.lineWidth = 0.8;
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    ctx.ellipse(x, y, 9, 4.5, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x - 3, y);
    ctx.lineTo(x + 3, y);
    ctx.moveTo(x, y - 2);
    ctx.lineTo(x, y + 2);
    ctx.stroke();
    ctx.restore();
  }

  function drawPulses(now) {
    state.pulses = state.pulses.filter((pulse) => now - pulse.start < 1200);
    for (const pulse of state.pulses) {
      const age = (now - pulse.start) / 1200;
      const [x, y] = world(pulse.x, pulse.y, 0);
      ctx.save();
      ctx.globalAlpha = 1 - age;
      ctx.strokeStyle = pulse.color;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.ellipse(x, y, 12 + age * 42, 6 + age * 21, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }

  function draw(timestamp) {
    if (!state.room) return;
    const rect = canvas.getBoundingClientRect();
    if (Math.abs(rect.width - view.width) > 0.5 || Math.abs(rect.height - view.height) > 0.5) {
      resize();
    }
    ctx.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
    ctx.clearRect(0, 0, view.width, view.height);
    ctx.save();
    ctx.translate(view.ox, view.oy);
    ctx.scale(view.scale, view.scale);

    drawFloor();
    drawGlow();
    drawWalls();
    drawZoneLabels();
    drawTarget();

    const items = drawFurniture();
    for (const spot of state.placements) {
      const item = device(spot.device_id);
      if (!item) continue;
      items.push({
        depth: spot.x + spot.y,
        draw: () => drawDevice(spot, item),
      });
    }
    items.push({
      depth: state.local.x + state.local.y + 0.1,
      draw: () => drawAvatar(state.local, { moving: state.local.moving }, timestamp),
    });
    for (const presence of state.remote.values()) {
      items.push({
        depth: presence.x + presence.y + 0.1,
        draw: () => drawAvatar(presence, { remote: true }, timestamp),
      });
    }
    items.sort((a, b) => a.depth - b.depth);
    for (const item of items) item.draw();
    drawPulses(timestamp);
    ctx.restore();

    const text = lastEventText(state.lastEvent);
    $("#last-event").textContent = text;
  }

  async function init() {
    bindUi();
    renderSuggestions();
    resize();
    window.requestAnimationFrame(loop);
    // Some embedded/browser preview panes pause rAF when the tab is not
    // frontmost. Keep the simulation deterministic with a low-rate fallback.
    window.setInterval(() => {
      const now = performance.now();
      if (now - lastFrame > 250) {
        const dt = Math.min((now - lastFrame) / 1000, 0.05);
        lastFrame = now;
        step(dt);
        draw(now);
      }
    }, 90);
    initSSE();
    try {
      await refreshState(true);
    } catch (error) {
      showNotice("SCENE / LOAD FAILED", error.message, "alert", 9000);
    }
    window.setInterval(() => {
      if (state.ready) refreshState(false).catch(() => {});
    }, 5000);
  }

  init();
})();
