(() => {
  "use strict";

  const TIER_ORDER = ["T3", "T2", "T1", "T0", "?"];
  const TIER_CLASS = { T3: "t3", T2: "t2", T1: "t1", T0: "t0", "?": "tu" };
  const TIER_NAME = { T3: "T3 边缘", T2: "T2 Linux", T1: "T1 微控", T0: "T0 端点", "?": "未确定" };
  const BUILTIN_SIM_IDS = new Set([
    "living_light",
    "desk_plug",
    "living_sensor",
    "living_curtain",
    "entry_door",
    "bedroom_purifier",
  ]);

  const state = {
    inventory: null,
    deep: null,
    devices: [],
    sim: [],
    access: null,
    bindings: [],
    events: [],
    scope: "all",
    selectedBinding: null,
    pulseTimer: null,
    stream: null,
    composing: false,
    catalog: null,
    eventCount: 0,
    tier: "all",
    query: "",
    selectedKey: null,
    selectedNode: null,
    topoNodes: [],
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);

  const tierOf = (device) => device?.analysis?.agent_tier || "?";
  const kindOf = (device) => device?.analysis?.kind || "未知设备";
  const nameOf = (device) =>
    device?.friendly_name || device?.hostname || device?.ip || device?.mac || "unknown";
  const ipOf = (device) => device?.ip || "";

  async function api(path, options) {
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

  function portMap() {
    const map = {};
    for (const host of state.deep?.hosts || []) {
      map[host.ip] = host.open_ports || [];
    }
    return map;
  }

  function deviceByKey(key) {
    return state.devices.find(
      (device) => device.ip === key || nameOf(device) === key
    );
  }

  function formatTime(value) {
    if (!value) return "—";
    return String(value).replace("T", " ").slice(0, 16);
  }

  function renderStats() {
    $("#stat-devices").textContent = state.inventory ? state.devices.length : "—";
    $("#stat-tiers").textContent = state.access?.adapters?.length ?? "—";
    $("#stat-sim").textContent = state.access?.devices ?? "—";
    $("#stat-capabilities").textContent = state.catalog?.tools?.length ?? "—";
    const ready = (state.access?.adapters || []).filter(item => item.available).length;
    $("#adapter-summary").textContent = state.access ? `${ready} 个驱动就绪 · 不代表硬件在线` : "运行时不可用";
    const policy = {supervised: "监督模式", read_only: "只读模式", autonomous: "自主模式"};
    $("#policy-summary").textContent = policy[state.access?.policy?.mode] || "策略未读取";
  }

  function renderRail() {
    const host = state.inventory?.host || {};
    const lines = [];
    if (host.cidr || host.subnet) lines.push(host.cidr || host.subnet);
    if (host.gateway) lines.push(`网关 ${host.gateway}`);
    if (host.local_ip) lines.push(`本机 ${host.local_ip}`);
    $("#rail-network").textContent = lines.length ? lines.join("\n") : "尚无扫描数据";

    const deepCount = (state.deep?.hosts || []).length;
    const stamp = formatTime(state.inventory?.generated_at);
    $("#rail-source").textContent =
      `发现 ${state.devices.length} 台 · 深扫 ${deepCount} 台 · ${stamp}`;

    const access = state.access || {};
    const tools = state.catalog?.tools || [];
    $("#rail-access").textContent =
      access.devices != null
        ? `${access.devices} 设备 · ${tools.length} 能力 · ${access.policy?.mode || "?"}`
        : "AccessHub 未启用";
  }

  const PROTOCOLS = {
    sim: ["Simulator", "内存模拟器", "SIM"],
    home_assistant: ["Home Assistant", "REST · 实体桥接", "HA"],
    mqtt: ["MQTT", "Publish / Subscribe", "MQ"],
    zigbee2mqtt: ["Zigbee2MQTT", "MQTT · Zigbee 桥接", "ZB"],
    ble: ["Bluetooth LE", "GATT · Read / Notify", "BLE"],
    nfc: ["NFC", "UID · NDEF / Tag", "NFC"],
  };

  function buildTopoNodes() {
    const adapters = state.access?.adapters || [];
    const catalog = state.access?.adapter_catalog?.length
      ? state.access.adapter_catalog : adapters;
    return catalog.map(spec => {
      const configured = adapters.find(item => item.name === spec.name);
      const devices = state.bindings.filter(item => item.adapter === spec.name);
      return {
        key: spec.name,
        label: PROTOCOLS[spec.name]?.[0] || spec.name,
        sub: PROTOCOLS[spec.name]?.[1] || "自定义协议",
        icon: PROTOCOLS[spec.name]?.[2] || "IO",
        enabled: Boolean(configured),
        available: Boolean(configured?.available),
        count: configured?.devices || 0,
        devices,
      };
    }).sort((a, b) => Number(b.enabled) - Number(a.enabled))
      .filter(item => state.scope === "all" || item.enabled);
  }

  function renderTopology() {
    const nodes = buildTopoNodes();
    state.topoNodes = nodes;
    const height = Math.max(300, nodes.length * 66 + 26);
    const center = height / 2;
    $("#topology-svg").setAttribute("viewBox", `0 0 1120 ${height}`);
    $("#topo-rings").innerHTML = "";
    if (!nodes.length) {
      $("#topo-links").innerHTML = "";
      $("#topo-nodes").innerHTML = `<text x="560" y="140" text-anchor="middle" class="graph-sub">${state.access ? "当前没有启用的适配器" : "无法读取运行时，请检查服务连接"}</text>`;
      $("#route-detail").innerHTML = '<p class="small">适配器启用后，绑定关系将显示在这里。</p>';
      $("#topo-caption").textContent = "无可显示的调用路径";
      return;
    }
    const connected = nodes.filter(item => item.available && item.count > 0);
    $("#topo-links").innerHTML = `<path class="graph-link graph-entry" d="M 208 ${center} H 291"/>` + nodes.map((node, i) => {
      const y = 26 + i * 66 + 25;
      const active = node.available && node.count > 0;
      return `<path data-route="${esc(node.key)}" class="graph-link ${active ? "" : "is-disabled"}" d="M 510 ${center} C 566 ${center}, 566 ${y}, 620 ${y}"/>
        <path data-route="${esc(node.key)}" class="graph-link ${active ? "" : "is-disabled"}" d="M 826 ${y} H 908"/>`;
    }).join("");
    $("#topo-rings").innerHTML = `
      <g transform="translate(24 ${center-69})"><rect class="graph-box" width="184" height="138" rx="12"/>
        <text x="18" y="27" class="graph-heading">NORTHBOUND</text><text x="18" y="56" class="graph-title">Agent / Client</text>
        <text x="18" y="80" class="graph-sub">统一的能力调用入口</text><rect x="18" y="99" width="148" height="24" rx="5" class="graph-chip"/>
        <text x="30" y="115" class="graph-code">MCP · HTTP · CLI</text></g>
      <g transform="translate(292 ${center-99})"><rect class="graph-box graph-box--hub" width="218" height="198" rx="13"/>
        <text x="20" y="28" class="graph-heading">SOUTHBOUND RUNTIME</text><text x="20" y="59" class="graph-title">AccessHub</text>
        <text x="20" y="84" class="graph-code">iot_call(device, capability)</text>
        <path d="M 20 101 H 198" stroke="var(--rule)"/>
        <text x="20" y="124" class="graph-sub">01  参数校验与能力匹配</text><text x="20" y="148" class="graph-sub">02  策略检查 · ${esc(state.access?.policy?.mode || "unknown")}</text>
        <text x="20" y="172" class="graph-sub">03  执行分派与审计记录</text></g>
      <text x="248" y="${center-13}" class="graph-sub" text-anchor="middle">调用</text>
      <text x="400" y="${center+126}" class="graph-sub" text-anchor="middle">← 状态回读 / 设备主动上报</text>`;
    $("#topo-nodes").innerHTML = nodes.map((node, i) => {
      const y = 26 + i * 66;
      const count = node.count;
      const simulated = node.key === "sim" || (node.devices.length > 0 && node.devices.every(d => d.metadata?.simulated || d.metadata?.virtual));
      const status = !node.enabled ? "未启用" : !node.available ? "驱动不可用" : count ? (simulated ? "模拟绑定" : "已绑定 · 待调用验证") : "尚未绑定设备";
      return `<g class="node ${node.available ? "" : "is-disabled"}" data-node="${esc(node.key)}" tabindex="0" role="button" aria-pressed="false" aria-label="${esc(node.label)}，${count} 个绑定，${status}" transform="translate(620 ${y})">
        <rect width="206" height="50" rx="8"/><text x="13" y="30" class="protocol-icon">${esc(node.icon)}</text>
        <text x="55" y="21" class="name">${esc(node.label)}</text><text x="55" y="38" class="sub">${esc(node.sub)}</text><circle cx="190" cy="12" r="3" class="node-status"/></g>
        <g transform="translate(912 ${y})"><text x="0" y="23" class="graph-device-count">${count.toString().padStart(2, "0")}</text><text x="34" y="23" class="graph-sub">${count ? "个设备" : "设备"}</text><text x="0" y="41" class="graph-device-note">${status}</text></g>`;
    }).join("");
    if (!nodes.some(node => node.key === state.selectedNode)) state.selectedNode = connected[0]?.key || nodes[0]?.key;
    $("#topo-caption").textContent = `${nodes.length} 个协议适配器 · 逻辑绑定视图`;
    syncTopoSelection();
    renderRouteDetail();
  }

  function syncTopoSelection() {
    document.querySelectorAll("#topo-nodes .node").forEach(node => {
      const selected = node.dataset.node === state.selectedNode;
      node.classList.toggle("is-selected", selected);
      node.setAttribute("aria-pressed", String(selected));
    });
    document.querySelectorAll("[data-route]").forEach(path => {
      path.classList.toggle("is-selected", path.dataset.route === state.selectedNode && !path.classList.contains("is-disabled"));
    });
  }

  function renderRouteDetail() {
    const node = state.topoNodes.find(item => item.key === state.selectedNode);
    if (!node) return;
    const caps = new Set(node.devices.flatMap(device => (device.capabilities || []).map(cap => cap.name)));
    const selected = node.devices.find(device => device.device_id === state.selectedBinding);
    $("#route-detail").innerHTML = `<div class="route-detail__head"><strong>${esc(node.label)}</strong><span>${node.count} 个绑定 · ${caps.size} 种能力</span><span class="badge">${node.enabled ? node.available ? "驱动就绪" : "驱动不可用" : "未启用"}</span></div>
      <div class="bound-devices">${node.devices.length ? node.devices.map(device => `<button class="bound-device" type="button" data-binding="${esc(device.device_id)}" aria-pressed="${device.device_id === state.selectedBinding}">${esc(device.name)}<small>${esc(device.kind)}</small>${device.metadata?.simulated || device.metadata?.virtual ? '<small class="sim-tag">模拟</small>' : ''}</button>`).join("") : `<span class="small">${node.enabled ? "暂无绑定设备，可从右上角接入。" : "在配置中启用该适配器后，即可接入设备。"}</span>`}</div>
      ${selected ? `<p class="binding-detail small"><span class="mono">${esc(selected.device_id)}</span> · ${esc(selected.protocol)} · 能力：${esc((selected.capabilities || []).map(cap => cap.name).join(" / "))}</p>` : ""}`;
  }

  function selectTopologyNode(node) {
    state.selectedNode = node.key;
    state.selectedBinding = null;
    syncTopoSelection();
    renderRouteDetail();
  }

  function notify(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(notify.timer);
    notify.timer = setTimeout(() => { toast.hidden = true; }, 4500);
  }

  function renderTierFilters() {
    const present = new Set(state.devices.map(tierOf));
    const tiers = ["all", ...TIER_ORDER.filter((tier) => present.has(tier))];
    $("#tier-filters").innerHTML = tiers
      .map(
        (tier) =>
          `<button class="chip" type="button" data-tier="${tier}" aria-pressed="${
            state.tier === tier
          }">${tier === "all" ? "全部" : TIER_NAME[tier]}</button>`
      )
      .join("");
    $("#fleet-search").value = state.query;
  }

  function renderFleet() {
    const ports = portMap();
    const query = state.query.trim().toLowerCase();
    let list = state.devices.filter((device) => {
      if (state.tier !== "all" && tierOf(device) !== state.tier) return false;
      if (!query) return true;
      const haystack = [
        nameOf(device),
        device.ip,
        device.mac,
        kindOf(device),
        (ports[device.ip] || []).join(" "),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });

    list.sort(
      (a, b) =>
        TIER_ORDER.indexOf(tierOf(a)) - TIER_ORDER.indexOf(tierOf(b)) ||
        nameOf(a).localeCompare(nameOf(b), "zh-CN")
    );

    $("#fleet-count").textContent = `${list.length} / ${state.devices.length} 台`;

    if (!list.length) {
      $("#fleet-list").innerHTML =
        '<p class="small" style="padding:12px 2px">没有匹配的设备。试试清空搜索或切换档位。</p>';
      return;
    }

    $("#fleet-list").innerHTML = list
      .map((device) => {
        const key = ipOf(device) || nameOf(device);
        const openPorts = ports[device.ip] || [];
        const portText = openPorts.length ? openPorts.join(",") : "—";
        const tierClass = TIER_CLASS[tierOf(device)] || "tu";
        return `<button class="fleet-row ${
          state.selectedKey === key ? "is-selected" : ""
        }" type="button" role="listitem" data-key="${esc(key)}">
            <span class="tier-badge ${tierClass}">${tierOf(device)}</span>
            <span class="fleet-row__main">
              <span class="fleet-row__name">${esc(nameOf(device))}</span>
              <span class="fleet-row__kind">${esc(kindOf(device))}</span>
            </span>
            <span class="fleet-row__meta">${esc(device.ip || "mDNS")}<span class="fleet-row__ports">${esc(
          portText
        )}</span></span>
          </button>`;
      })
      .join("");
  }

  function renderDetail(device) {
    if (!device) {
      $("#fleet-detail").hidden = true;
      return;
    }
    const analysis = device.analysis || {};
    const ports = portMap()[device.ip] || [];
    const detail = $("#fleet-detail");
    detail.hidden = false;
    detail.innerHTML = `<h3>${esc(nameOf(device))}</h3>
      <dl>
        <dt>IP / MAC</dt><dd class="mono">${esc(device.ip || "—")} / ${esc(device.mac || "—")}</dd>
        <dt>类型</dt><dd>${esc(kindOf(device))} · ${esc(tierOf(device))}</dd>
        <dt>芯片</dt><dd>${esc(analysis.chip_hint || "—")}</dd>
        <dt>端口</dt><dd class="mono">${ports.length ? esc(ports.join(", ")) : "—"}</dd>
        <dt>数据</dt><dd>${esc((analysis.data_paths || []).join(" / ") || "—")}</dd>
        <dt>控制</dt><dd>${esc((analysis.control_paths || []).join(" / ") || "—")}</dd>
        <dt>备注</dt><dd>${esc(analysis.notes || "—")}</dd>
      </dl>`;
  }

  function switchMarkup(device) {
    return `<button class="switch" type="button" data-action="toggle"
      aria-pressed="${Boolean(device.state.on)}" aria-label="${esc(device.name)} 开关">
      <span></span></button>`;
  }

  function simBody(device) {
    const state = device.state;
    switch (device.kind) {
      case "light":
        return `
          <p class="sim-value">${state.brightness}<span>% 亮度</span></p>
          <div class="sim-controls">
            ${switchMarkup(device)}
            <span class="sim-label">${state.on ? "已开" : "已关"} · ${state.power_w} W</span>
          </div>
          <input type="range" min="1" max="100" value="${state.brightness}"
            data-action="set_brightness" aria-label="${esc(device.name)} 亮度">`;
      case "plug":
        return `
          <p class="sim-value">${state.power_w}<span>W</span></p>
          <div class="sim-controls">
            ${switchMarkup(device)}
            <span class="sim-label">${state.on ? "供电中" : "已断电"}</span>
          </div>`;
      case "sensor":
        return `
          <div class="sim-pair">
            <div><span class="sim-label">温度</span><p class="sim-value sim-value--small">${state.temperature}<span>°C</span></p></div>
            <div><span class="sim-label">湿度</span><p class="sim-value sim-value--small">${state.humidity}<span>%</span></p></div>
          </div>
          <p class="sim-label">电量 ${state.battery}% · 由 Zigbee 上报</p>`;
      case "cover":
        return `
          <p class="sim-value">${Math.round(state.position)}<span>% 位置</span></p>
          <input type="range" min="0" max="100" value="${Math.round(state.position)}"
            data-action="set_position" aria-label="${esc(device.name)} 位置">
          <div class="sim-controls">
            <button class="mini" type="button" data-action="open">打开</button>
            <button class="mini" type="button" data-action="close">关闭</button>
            <span class="sim-label">${state.moving ? "移动中" : "停止"}</span>
          </div>`;
      case "binary_sensor":
        return `
          <p class="sim-value sim-value--small">${state.open ? "打开" : "关闭"}</p>
          <p class="sim-label">电量 ${state.battery}% · 门磁事件</p>`;
      case "purifier":
        return `
          <p class="sim-value">${state.pm25}<span>PM2.5</span></p>
          <div class="sim-controls">
            ${switchMarkup(device)}
            <span class="sim-label">${state.on ? "运行中" : "待机"} · ${state.mode}</span>
          </div>`;
      default:
        return `<p class="sim-label">${esc(JSON.stringify(state))}</p>`;
    }
  }

  function simTile(device) {
    const active =
      device.kind === "binary_sensor"
        ? Boolean(device.state.open)
        : device.kind === "sensor"
        ? true
        : Boolean(device.state.on);
    const tierClass = TIER_CLASS[device.tier] || "tu";
    return `<article class="sim-tile" data-id="${esc(device.id)}">
        <div class="sim-tile__top">
          <div>
            <h3>${esc(device.name)}</h3>
            <p class="sim-tile__meta">${esc(device.room)} · ${esc(device.protocol)} · ${esc(device.tier)}</p>
          </div>
          <span class="status-dot ${active ? "is-on" : ""}"></span>
        </div>
        <div class="sim-tile__body">${simBody(device)}</div>
        <div class="sim-tile__footer">
          <span class="tier-badge ${tierClass}">${esc(device.tier)}</span>
          ${
            BUILTIN_SIM_IDS.has(device.id)
              ? ""
              : '<button class="mini mini--danger" type="button" data-action="decommission">移除</button>'
          }
        </div>
      </article>`;
  }

  function renderSim() {
    const grid = $("#sim-devices");
    grid.innerHTML = state.sim.length
      ? state.sim.map(simTile).join("")
      : '<p class="small">模拟器未启动。</p>';
  }

  async function act(deviceId, action, value) {
    const tile = document.querySelector(`.sim-tile[data-id="${CSS.escape(deviceId)}"]`);
    if (tile) tile.classList.add("is-live");
    try {
      const payload = await api(`/api/sim/devices/${encodeURIComponent(deviceId)}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, value }),
      });
      const index = state.sim.findIndex((item) => item.id === deviceId);
      if (index >= 0 && payload.device) state.sim[index] = payload.device;
      renderSim();
    } catch (error) {
      console.error("sim action failed", error);
      notify(`操作失败：${error.message}`);
    } finally {
      window.setTimeout(() => {
        const current = document.querySelector(`.sim-tile[data-id="${CSS.escape(deviceId)}"]`);
        if (current) current.classList.remove("is-live");
      }, 650);
    }
  }

  async function refreshSim() {
    try {
      const data = await api("/api/sim/devices");
      state.sim = data.devices || [];
      renderSim();
      renderStats();
    } catch (error) {
      console.error("sim refresh failed", error);
    }
  }

  async function refreshAccess() {
    try {
      const [status, catalog, bindings] = await Promise.all([
        api("/api/access/status"),
        api("/api/access/catalog"),
        api("/api/access/devices"),
      ]);
      state.access = status || {};
      state.catalog = catalog || { tools: [] };
      state.bindings = bindings.devices || [];
      renderRail();
      renderStats();
      renderTopology();
    } catch (error) {
      console.error("access refresh failed", error);
    }
  }

  async function decommissionDevice(deviceId) {
    try {
      await api("/api/northbound/decommission", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: deviceId, confirm: true }),
      });
      await refreshSim();
      await refreshAccess();
    } catch (error) {
      console.error("decommission failed", error);
      notify(`移除失败：${error.message}`);
    }
  }

  function updateOnboardFields() {
    const adapter = $("#onboard-adapter").value;
    const spec = (state.access?.adapter_catalog || []).find(item => item.name === adapter);
    document.querySelectorAll("#onboard-form [data-field]").forEach((field) => {
      field.hidden = !(spec?.fields || []).includes(field.dataset.field);
    });
    const kinds = $("#onboard-form").kind;
    const selected = kinds.value;
    kinds.replaceChildren(...(spec?.kinds || []).map(kind => new Option(kind, kind)));
    if ((spec?.kinds || []).includes(selected)) kinds.value = selected;
  }

  async function openOnboard() {
    await refreshAccess();
    const form = $("#onboard-form");
    form.reset();
    const enabled = new Set((state.access?.adapters || []).map(item => item.name));
    form.adapter.replaceChildren(...(state.access?.adapter_catalog || [])
      .filter(item => enabled.has(item.name))
      .map(item => new Option(item.name, item.name)));
    form.name.value = "新设备";
    form.room.value = "客厅";
    form.protocol.value = "";
    form.tier.value = "T1";
    form.capabilities.value = "read";
    form.confirm.checked = true;
    form.state.value = '{"temperature":22.5,"humidity":45,"battery":90}';
    form.device_id.value = "";
    updateOnboardFields();
    const error = $("#onboard-error");
    error.hidden = true;
    error.textContent = "";
    $("#onboard-dialog").showModal();
  }

  async function submitOnboard(event) {
    event.preventDefault();
    const form = event.target;
    const error = $("#onboard-error");
    const adapter = form.adapter.value;
    const body = {
      adapter,
      device_id: form.device_id.value.trim(),
      name: form.name.value.trim(),
      kind: form.kind.value,
      room: form.room.value.trim(),
      protocol: form.protocol.value.trim(),
      tier: form.tier.value,
      confirm: form.confirm.checked,
      safe_to_automate: false,
    };
    const capabilities = form.capabilities.value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    if (capabilities.length) body.capabilities = capabilities;
    const spec = (state.access?.adapter_catalog || []).find(item => item.name === adapter);
    for (const field of spec?.fields || []) {
      const value = form.elements.namedItem(field)?.value?.trim();
      if (!value) continue;
      if (field === "state" || field === "connection") {
        try {
          body[field] = JSON.parse(value);
          if (!body[field] || Array.isArray(body[field]) || typeof body[field] !== "object") {
            throw new Error("object required");
          }
        } catch (_) {
          error.textContent = `${field} 必须是合法 JSON 对象。`;
          error.hidden = false;
          return;
        }
      } else {
        body[field] = value;
      }
    }
    if (!body.confirm) {
      error.textContent = "需要勾选确认才能接入。";
      error.hidden = false;
      return;
    }
    error.hidden = true;
    try {
      await api("/api/northbound/commission", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      $("#onboard-dialog").close();
      await refreshSim();
      await refreshAccess();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  }

  async function pollSim() {
    if (
      document.activeElement &&
      document.activeElement.closest &&
      document.activeElement.closest("#sim-devices")
    ) {
      return;
    }
    await refreshSim();
  }

  function renderSuggestions() {
    const goals = [
      "打开客厅灯并关掉书房插座",
      "把客厅灯调到 30%",
      "现在温湿度",
      "打开窗帘",
      "家里状态",
    ];
    $("#suggestions").innerHTML = goals
      .map(
        (goal) =>
          `<button class="suggestion" type="button" data-goal="${esc(goal)}">${esc(goal)}</button>`
      )
      .join("");
  }

  async function runGoal(goal) {
    if ($("#agent-run").disabled || state.composing) return;
    const button = $("#agent-run");
    const trace = $("#agent-trace");
    button.disabled = true;
    button.textContent = "执行中…";
    trace.innerHTML = '<p class="trace__empty">正在规划并调用工具…</p>';
    try {
      const result = await api("/api/northbound/intent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal }),
      });
      renderTrace(result);
      await refreshSim();
    } catch (error) {
      trace.innerHTML = `<p class="summary is-error">执行失败：${esc(error.message)}</p>`;
    } finally {
      button.disabled = false;
      button.textContent = "执行";
    }
  }

  function renderTrace(result) {
    const steps = result.steps || [];
    const summaryClass = result.ok ? "summary" : "summary is-error";
    const summary = `<p class="${summaryClass}">${esc(result.summary)}</p>`;
    const stepMarkup = steps
      .map((step, index) => {
        const args =
          step.args && Object.keys(step.args).length
            ? " " + JSON.stringify(step.args)
            : "";
        return `<article class="step" data-n="${step.n}" style="--i:${index}">
            <p class="step__title">${esc(step.title)}</p>
            <p class="step__detail">${esc(step.detail)}</p>
            <p class="step__tool">${esc(step.tool)}${esc(args)}</p>
          </article>`;
      })
      .join("");
    $("#agent-trace").innerHTML = summary + stepMarkup;
  }

  function bindEvents() {
    $("#agent-input").addEventListener("compositionstart", () => { state.composing = true; });
    $("#agent-input").addEventListener("compositionend", () => { state.composing = false; });
    $("#agent-input").addEventListener("keydown", event => {
      if (event.isComposing || state.composing || event.keyCode === 229) return;
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        const goal = event.target.value.trim();
        if (goal) runGoal(goal);
      }
    });
    $("#agent-run").addEventListener("click", () => {
      const goal = $("#agent-input").value.trim();
      if (goal) runGoal(goal);
    });

    $("#agent-form").addEventListener("submit", (event) => {
      event.preventDefault();
      if (state.composing) return;
      const goal = $("#agent-input").value.trim();
      if (goal) runGoal(goal);
    });

    $("#suggestions").addEventListener("click", (event) => {
      const button = event.target.closest("[data-goal]");
      if (!button) return;
      $("#agent-input").value = button.dataset.goal;
      runGoal(button.dataset.goal);
    });

    $("#fleet-search").addEventListener("input", (event) => {
      state.query = event.target.value;
      renderFleet();
    });

    $("#tier-filters").addEventListener("click", (event) => {
      const button = event.target.closest("[data-tier]");
      if (!button) return;
      state.tier = button.dataset.tier;
      renderTierFilters();
      renderFleet();
    });

    $("#fleet-list").addEventListener("click", (event) => {
      const row = event.target.closest("[data-key]");
      if (!row) return;
      state.selectedKey = row.dataset.key;
      renderFleet();
      renderDetail(deviceByKey(row.dataset.key));
    });

    $("#topo-nodes").addEventListener("click", (event) => {
      const node = event.target.closest(".node");
      if (!node) return;
      const data = state.topoNodes.find((item) => item.key === node.dataset.node);
      if (data) selectTopologyNode(data);
    });

    $("#topo-nodes").addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const node = event.target.closest(".node");
      if (!node) return;
      event.preventDefault();
      const data = state.topoNodes.find((item) => item.key === node.dataset.node);
      if (data) selectTopologyNode(data);
    });

    $("#topology-scope").addEventListener("click", event => {
      const button = event.target.closest("[data-scope]");
      if (!button) return;
      state.scope = button.dataset.scope;
      document.querySelectorAll("[data-scope]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
      renderTopology();
    });
    $("#route-detail").addEventListener("click", event => {
      const button = event.target.closest("[data-binding]");
      if (!button) return;
      state.selectedBinding = state.selectedBinding === button.dataset.binding ? null : button.dataset.binding;
      renderRouteDetail();
    });
    document.querySelectorAll(".nav a").forEach(link => link.addEventListener("click", () => {
      document.querySelectorAll(".nav a").forEach(item => item.classList.toggle("is-active", item === link));
    }));
    $("#onboard-open").addEventListener("click", openOnboard);
    $("#onboard-close").addEventListener("click", () =>
      $("#onboard-dialog").close()
    );
    $("#onboard-cancel").addEventListener("click", () =>
      $("#onboard-dialog").close()
    );
    $("#onboard-adapter").addEventListener("change", updateOnboardFields);
    $("#onboard-form").addEventListener("submit", submitOnboard);

    $("#sim-devices").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const tile = button.closest(".sim-tile");
      if (!tile) return;
      if (button.dataset.action === "decommission") {
        decommissionDevice(tile.dataset.id);
        return;
      }
      act(tile.dataset.id, button.dataset.action);
    });

    $("#sim-devices").addEventListener("change", (event) => {
      const input = event.target.closest("input[type='range'][data-action]");
      if (!input) return;
      const tile = input.closest(".sim-tile");
      if (!tile) return;
      act(tile.dataset.id, input.dataset.action, Number(input.value));
    });

    $("#sim-reset").addEventListener("click", async () => {
      try {
        const data = await api("/api/sim/reset", { method: "POST" });
        state.sim = data.devices || [];
        renderSim();
        renderStats();
        renderTrace({
          ok: true,
          summary: "模拟端点已重置到初始状态。",
          steps: [],
        });
      } catch (error) {
        console.error("reset failed", error);
      }
    });
  }

  function shorten(text, limit = 42) {
    const value = String(text || "");
    return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
  }

  function flowLabel(event) {
    switch (event.type) {
      case "intent":
        return `northbound · intent · ${shorten(event.goal)}`;
      case "intent.result":
        return `northbound · result · ${shorten(event.summary)}`;
      case "command":
        return `southbound · ${shorten(event.name || event.device_id, 18)} · ${
          event.capability
        } ${event.ok ? "✓" : "✗"}`;
      case "state":
        return `northbound · state · ${shorten(event.name || event.device_id, 18)} · ${
          event.capability
        }`;
      case "device.state":
      case "device.notification":
      case "device.tag":
      case "device.connection":
      case "device.error":
        return `${event.simulated ? "模拟 · " : ""}${event.type} · ${shorten(event.name || event.device_id, 24)}`;
      case "commission.request":
        return `northbound · commission · ${shorten(
          event.name || event.device_id,
          20
        )}`;
      case "commission":
        return `southbound · commission · ${shorten(
          event.name || event.device_id,
          18
        )} ${event.ok ? "✓" : "✗"}`;
      case "commission.result":
        return `northbound · ready · ${shorten(
          event.name || event.device_id,
          18
        )} · ${(event.capabilities || []).length} capabilities`;
      case "decommission":
        return `southbound · remove · ${shorten(
          event.name || event.device_id,
          18
        )} ${event.ok ? "✓" : "✗"}`;
      case "decommission.result":
        return `northbound · removed · ${shorten(
          event.name || event.device_id,
          18
        )}`;
      case "reset":
        return "southbound · simulator reset";
      default:
        return `${event.plane || ""} · ${event.type || "event"}`;
    }
  }

  function renderEventList() {
    const list = $("#event-list");
    const open = new Set(Array.from(list.querySelectorAll("details[open]")).map(item => item.dataset.event));
    const scroll = list.scrollTop;
    const height = list.scrollHeight;
    if (!state.events.length) return;
    list.innerHTML = state.events.slice().reverse().map(event => {
      const title = event.error || event.summary || event.goal || event.name || event.device_id || "运行时事件";
      const time = String(event.ts || "").slice(11,19) || "—";
      return `<details class="event-item ${event.ok === false ? "is-error" : ""}" data-event="${event.id}" ${open.has(String(event.id)) ? "open" : ""}><summary><span><span class="event-title">${esc(shorten(title, 50))}</span><span class="event-sub">${esc(event.type)}${event.capability ? ` · ${esc(event.capability)}` : ""}${event.simulated ? " · 模拟" : ""}</span></span><time>${esc(time)}</time></summary><pre>${esc(JSON.stringify(event, null, 2))}</pre></details>`;
    }).join("");
    if (scroll > 0) list.scrollTop = scroll + list.scrollHeight - height;
  }

  function renderFlowEvent(event, animate) {
    if (state.events.some(item => item.id === event.id)) return;
    state.events.push(event);
    state.events.sort((a,b) => a.id-b.id);
    state.events = state.events.slice(-20);
    state.eventCount = Math.max(state.eventCount, event.id || 0);
    $("#flow-last").textContent = flowLabel(event);
    $("#flow-last").title = flowLabel(event);
    $("#flow-count").textContent = `${state.eventCount} events`;
    renderEventList();
    if (!animate) return;
    const adapter = event.adapter || state.bindings.find(device => device.device_id === event.device_id)?.adapter;
    const incoming = event.direction === "up" || event.direction === "out";
    if (adapter && event.ok !== false) {
      document.querySelectorAll(".graph-link.is-live").forEach(path => path.classList.remove("is-live", "is-return"));
      document.querySelectorAll("[data-route]").forEach(path => {
        if (path.dataset.route === adapter) {
          path.classList.add("is-live");
          path.classList.toggle("is-return", incoming);
        }
      });
      $(".graph-entry")?.classList.add("is-live");
      $(".graph-entry")?.classList.toggle("is-return", incoming);
    }
    clearTimeout(state.pulseTimer);
    state.pulseTimer = setTimeout(() => document.querySelectorAll(".graph-link.is-live").forEach(path => path.classList.remove("is-live", "is-return")), 1400);
    const plane = event.plane === "southbound" ? "south" : "north";
    const track = document.querySelector(`.flowbar__track[data-track="${plane}"]`);
    if (track) {
      track.classList.remove("is-active", "is-reverse");
      void track.offsetWidth;
      track.classList.toggle("is-reverse", incoming);
      track.classList.add("is-active");
    }
    if (["commission.result", "decommission.result"].includes(event.type)) refreshAccess();
  }

  function initEvents() {
    const status = $("#stream-status");
    if (!window.EventSource) {
      status.textContent = "浏览器不支持事件流";
      return;
    }
    const source = new EventSource("/api/events");
    state.stream = source;
    source.onopen = () => {
      status.textContent = "事件流已连接";
      status.className = "stream-status is-connected";
      // Reconcile buffered events after reconnect; IDs prevent duplicate rows.
      api("/api/events/recent?limit=20").then(data => {
        const events = data.events || [];
        const newest = events.at(-1)?.id || 0;
        if (newest < state.eventCount) {
          state.events = [];
          state.eventCount = 0;
          $("#event-list").innerHTML = '<p class="small">运行时已重启，等待新事件。</p>';
          $("#flow-count").textContent = "0 events";
        }
        for (const event of events) renderFlowEvent(event, false);
      }).catch(() => {});
    };
    source.onerror = () => {
      status.textContent = "事件流重连中";
      status.className = "stream-status is-error";
    };
    source.onmessage = message => {
      try { renderFlowEvent(JSON.parse(message.data), true); }
      catch (error) { console.error("event render failed", error); }
    };
    window.addEventListener("pagehide", () => source.close());
  }

  async function init() {
    bindEvents();
    renderSuggestions();
    try {
      const [inventory, deep, sim, access, catalog, bindings] = await Promise.all([
        api("/api/inventory").catch(() => ({})),
        api("/api/deep_scan").catch(() => ({})),
        api("/api/sim/devices").catch(() => ({ devices: [] })),
        api("/api/access/status").catch(() => null),
        api("/api/access/catalog").catch(() => null),
        api("/api/access/devices").catch(() => ({ devices: [] })),
      ]);
      state.inventory = inventory || {};
      state.devices = state.inventory.devices || [];
      state.deep = deep || {};
      state.sim = sim.devices || [];
      state.access = access;
      state.bindings = bindings.devices || [];
      state.catalog = catalog || { tools: [] };
      renderStats();
      renderRail();
      renderTopology();
      renderTierFilters();
      renderFleet();
      renderSim();
      initEvents();
      window.setInterval(pollSim, 4000);
    } catch (error) {
      console.error("init failed", error);
      $("#agent-trace").innerHTML = `<p class="summary is-error">无法加载演示数据：${esc(
        error.message
      )}</p>`;
    }
  }

  init();
})();
