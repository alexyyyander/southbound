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
    $("#stat-devices").textContent = state.devices.length || "—";
    const tiers = new Set(state.devices.map(tierOf));
    $("#stat-tiers").textContent = state.devices.length ? tiers.size : "—";
    $("#stat-sim").textContent = state.sim.length || "—";
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

  function buildTopoNodes() {
    const gatewayIp = state.inventory?.host?.gateway || "";
    const gateway = state.devices.find((device) => device.ip === gatewayIp);
    const nas = state.devices.find((device) => kindOf(device).includes("NAS"));
    const printer = state.devices.find((device) => kindOf(device).includes("打印机"));
    const esp32 = state.devices.find((device) => kindOf(device).includes("ESP32"));
    const macs = state.devices.filter(
      (device) => kindOf(device).includes("Mac") || device.is_self
    );
    const phones = state.devices.filter((device) => kindOf(device).includes("随机 MAC"));
    const routers = state.devices.filter(
      (device) =>
        kindOf(device).includes("路由器/网关") && device.ip !== gatewayIp
    );
    const others = state.devices.filter((device) =>
      ["未知设备", "Wi-Fi 模组设备", "蜂窝模块/CPE", "智能屏/电视"].some((kind) =>
        kindOf(device).includes(kind)
      )
    );

    const nodes = [
      {
        key: "gateway",
        x: 500,
        y: 280,
        r: 48,
        label: "主路由",
        sub: gateway?.ip || gatewayIp || "gateway",
        icon: "router",
        tier: gateway ? tierOf(gateway) : "T0",
        count: null,
        filter: { tier: "T0", query: "路由器" },
      },
      {
        key: "aps",
        x: 500,
        y: 98,
        r: 30,
        label: `AP ×${routers.length}`,
        sub: routers.length ? "FTTR / 子路由" : "未发现",
        icon: "router",
        tier: "T0",
        count: routers.length || null,
        filter: { tier: "T0", query: "路由器" },
      },
      {
        key: "nas",
        x: 220,
        y: 150,
        r: 34,
        label: "NAS",
        sub: nas?.ip || "未发现",
        icon: "nas",
        tier: nas ? tierOf(nas) : "T2",
        count: null,
        filter: { query: "NAS" },
      },
      {
        key: "printer",
        x: 780,
        y: 150,
        r: 34,
        label: "打印机",
        sub: printer?.ip || "未发现",
        icon: "printer",
        tier: printer ? tierOf(printer) : "T0",
        count: null,
        filter: { query: "打印机" },
      },
      {
        key: "esp32",
        x: 840,
        y: 320,
        r: 34,
        label: "ESP32",
        sub: esp32?.ip || "未发现",
        icon: "chip",
        tier: esp32 ? tierOf(esp32) : "T1",
        count: null,
        filter: { query: "ESP32" },
      },
      {
        key: "macs",
        x: 210,
        y: 350,
        r: 40,
        label: `Mac ×${macs.length}`,
        sub: "T3 · 编排候选",
        icon: "laptop",
        tier: "T3",
        count: macs.length || null,
        filter: { tier: "T3" },
      },
      {
        key: "phones",
        x: 470,
        y: 470,
        r: 36,
        label: `手机/平板 ×${phones.length}`,
        sub: "随机 MAC",
        icon: "phone",
        tier: "T0",
        count: phones.length || null,
        filter: { query: "随机 MAC" },
      },
      {
        key: "others",
        x: 780,
        y: 470,
        r: 32,
        label: `其他 ×${others.length}`,
        sub: "模组 / 智能屏 / CPE",
        icon: "chip",
        tier: "?",
        count: others.length || null,
        filter: { query: "未知" },
      },
    ];

    return nodes.filter(
      (node) =>
        !["aps", "nas", "printer", "esp32", "macs", "phones", "others"].includes(node.key) ||
        node.count !== 0 ||
        node.sub !== "未发现"
    );
  }

  function renderTopology() {
    const nodes = buildTopoNodes();
    state.topoNodes = nodes;
    const linkLayer = $("#topo-links");
    const ringLayer = $("#topo-rings");
    const nodeLayer = $("#topo-nodes");

    ringLayer.innerHTML = state.devices.length
      ? `<circle class="ring" cx="500" cy="280" r="72"></circle><circle class="ring" cx="500" cy="280" r="108"></circle>`
      : "";

    if (!state.devices.length) {
      linkLayer.innerHTML = "";
      nodeLayer.innerHTML = `<text x="500" y="260" text-anchor="middle" fill="#70848b" font-size="13">尚无扫描数据</text>`;
      $("#topo-caption").textContent = "运行 discover.py 后刷新页面";
      $("#topo-time").textContent = "—";
      return;
    }

    const gateway = nodes.find((node) => node.key === "gateway");
    const linkMarkup = [];
    for (const node of nodes) {
      if (!gateway || node.key === "gateway") continue;
      linkMarkup.push(
        `<line class="link" x1="${gateway.x}" y1="${gateway.y}" x2="${node.x}" y2="${node.y}"></line>`
      );
    }
    const ap = nodes.find((node) => node.key === "aps");
    const phones = nodes.find((node) => node.key === "phones");
    if (ap && phones) {
      linkMarkup.push(
        `<line class="link link--dashed" x1="${ap.x}" y1="${ap.y}" x2="${phones.x}" y2="${phones.y}"></line>`
      );
    }
    linkLayer.innerHTML = linkMarkup.join("");

    const linkLabels = [];
    const addLinkLabel = (fromKey, toKey, text) => {
      const from = nodes.find((node) => node.key === fromKey);
      const to = nodes.find((node) => node.key === toKey);
      if (!from || !to) return;
      const x = (from.x + to.x) / 2;
      const y = (from.y + to.y) / 2 - 5;
      linkLabels.push(
        `<text class="link-label" x="${x}" y="${y}" text-anchor="middle">${esc(text)}</text>`
      );
    };
    addLinkLabel("gateway", "nas", "Ethernet");
    addLinkLabel("gateway", "printer", "Wi-Fi");
    addLinkLabel("gateway", "esp32", "Wi-Fi 2.4G");
    addLinkLabel("aps", "phones", "FTTR");
    linkLayer.innerHTML += linkLabels.join("");

    nodeLayer.innerHTML = nodes
      .map((node, index) => {
        const tierClass = TIER_CLASS[node.tier] || "tu";
        const iconSize = node.r * 0.92;
        const inner =
          node.count !== null
            ? `<text class="count" y="1">${node.count}</text>`
            : `<use href="#icon-${node.icon}" x="${-iconSize / 2}" y="${-iconSize / 2}" width="${iconSize}" height="${iconSize}" class="icon"></use>`;
        return `<g class="node ${tierClass}" data-node="${node.key}" tabindex="0" role="button"
            aria-label="${esc(node.label)}" style="--i:${index}"
            transform="translate(${node.x} ${node.y})">
            <circle class="halo" r="${node.r + 11}"></circle>
            <circle class="dot" r="${node.r}"></circle>
            ${inner}
            <text class="name" y="${node.r + 21}">${esc(node.label)}</text>
            <text class="sub" y="${node.r + 35}">${esc(node.sub)}</text>
          </g>`;
      })
      .join("");

    const tierCount = new Set(state.devices.map(tierOf)).size;
    $("#topo-caption").textContent =
      `${state.devices.length} 台设备 · ${tierCount} 个档位 · 节点颜色表示档位`;
    $("#topo-time").textContent = formatTime(state.inventory?.generated_at);
    syncTopoSelection();
  }

  function syncTopoSelection() {
    for (const node of document.querySelectorAll("#topo-nodes .node")) {
      node.classList.toggle("is-selected", node.dataset.node === state.selectedNode);
    }
  }

  function selectTopologyNode(node) {
    state.selectedNode = node.key;
    state.tier = node.filter.tier || "all";
    state.query = node.filter.query || "";
    syncTopoSelection();
    renderTierFilters();
    renderFleet();
    const fleet = $(".fleet");
    if (fleet) fleet.scrollIntoView({ behavior: "smooth", block: "start" });
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
      const [status, catalog] = await Promise.all([
        api("/api/access/status"),
        api("/api/access/catalog"),
      ]);
      state.access = status || {};
      state.catalog = catalog || { tools: [] };
      renderRail();
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
    $("#agent-run").addEventListener("click", () => {
      const goal = $("#agent-input").value.trim();
      if (goal) runGoal(goal);
    });

    $("#agent-form").addEventListener("submit", (event) => {
      event.preventDefault();
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

  function renderFlowEvent(event, animate) {
    if (event.id) state.eventCount = Math.max(state.eventCount, event.id);
    else state.eventCount += 1;
    $("#flow-last").textContent = flowLabel(event);
    $("#flow-last").title = flowLabel(event);
    $("#flow-count").textContent = `${state.eventCount} events`;
    if (!animate) return;
    const plane = event.plane === "southbound" ? "south" : "north";
    const track = document.querySelector(
      `.flowbar__track[data-track="${plane}"]`
    );
    if (!track) return;
    track.classList.remove("is-active", "is-reverse");
    if (event.direction === "up" || event.direction === "out") {
      track.classList.add("is-reverse");
    }
    void track.offsetWidth;
    track.classList.add("is-active");
    window.setTimeout(() => {
      track.classList.remove("is-active", "is-reverse");
    }, 800);
  }

  function initEvents() {
    api("/api/events/recent?limit=1")
      .then((data) => {
        const events = data.events || [];
        if (events.length) renderFlowEvent(events[events.length - 1], false);
      })
      .catch(() => {});
    if (!window.EventSource) return;
    const source = new EventSource("/api/events");
    source.onmessage = (message) => {
      try {
        renderFlowEvent(JSON.parse(message.data), true);
      } catch (_) {
        /* ignore malformed SSE frames */
      }
    };
  }

  async function init() {
    bindEvents();
    renderSuggestions();
    initEvents();
    try {
      const [inventory, deep, sim, access, catalog] = await Promise.all([
        api("/api/inventory").catch(() => ({})),
        api("/api/deep_scan").catch(() => ({})),
        api("/api/sim/devices").catch(() => ({ devices: [] })),
        api("/api/access/status").catch(() => ({})),
        api("/api/access/catalog").catch(() => ({ tools: [] })),
      ]);
      state.inventory = inventory || {};
      state.devices = state.inventory.devices || [];
      state.deep = deep || {};
      state.sim = sim.devices || [];
      state.access = access || {};
      state.catalog = catalog || { tools: [] };
      renderStats();
      renderRail();
      renderTopology();
      renderTierFilters();
      renderFleet();
      renderSim();
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
