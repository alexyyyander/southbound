const state = {
  data: null,
  busy: false,
};

const $ = (selector) => document.querySelector(selector);
const NS = "http://www.w3.org/2000/svg";

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = {};
  }
  if (!response.ok) {
    throw new Error(data.error || response.statusText || `HTTP ${response.status}`);
  }
  return data;
}

function svgEl(name, attrs) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function drawArm(arm) {
  const svg = svgEl("svg", {
    viewBox: "0 0 260 180",
    "aria-label": `${arm.name} 姿态`,
    class: "arm-svg",
  });
  const pose = arm.state.pose || { x: 50, y: 50, z: 50 };
  const baseX = 130;
  const baseY = 152;
  const x = Number(pose.x ?? 50);
  const y = Number(pose.y ?? 50);
  const z = Number(pose.z ?? 50);
  const angle1 = -Math.PI / 2 + ((x - 50) / 100) * 0.75;
  const angle2 = angle1 + 0.35 + ((y - 50) / 100) * 0.9;
  const length1 = 46 + z * 0.18;
  const length2 = 38 + z * 0.14;
  const elbowX = baseX + Math.cos(angle1) * length1;
  const elbowY = baseY + Math.sin(angle1) * length1;
  const endX = elbowX + Math.cos(angle2) * length2;
  const endY = elbowY + Math.sin(angle2) * length2;

  svg.append(
    svgEl("ellipse", { cx: baseX, cy: baseY + 8, rx: 36, ry: 8, fill: "#dce9e4" }),
    svgEl("line", {
      x1: baseX, y1: baseY, x2: elbowX, y2: elbowY,
      stroke: "#405a63", "stroke-width": 10, "stroke-linecap": "round",
    }),
    svgEl("line", {
      x1: elbowX, y1: elbowY, x2: endX, y2: endY,
      stroke: "#0f7d78", "stroke-width": 7, "stroke-linecap": "round",
    }),
    svgEl("circle", { cx: baseX, cy: baseY, r: 15, fill: "#14232a" }),
    svgEl("circle", { cx: elbowX, cy: elbowY, r: 8, fill: "#e4f3ef", stroke: "#0f7d78", "stroke-width": 3 })
  );

  if (arm.role === "lamp") {
    const light = arm.state.light || {};
    const color = light.on ? "#f4c95d" : "#b9c7c3";
    const glow = svgEl("circle", { cx: endX, cy: endY, r: 27, fill: color, opacity: light.on ? 0.18 : 0.04 });
    const shade = svgEl("path", {
      d: `M ${endX - 16} ${endY} L ${endX + 16} ${endY} L ${endX + 8} ${endY + 22} L ${endX - 8} ${endY + 22} Z`,
      fill: color, stroke: "#405a63", "stroke-width": 1,
    });
    svg.append(glow, shade);
  } else if (arm.role === "display") {
    const display = arm.state.display || {};
    const group = svgEl("g", { transform: `translate(${endX} ${endY}) rotate(${angle2 * 57.3 + 90})` });
    group.append(
      svgEl("rect", { x: -25, y: -34, width: 50, height: 34, rx: 4, fill: display.on ? "#102b33" : "#dbe6e2", stroke: "#405a63", "stroke-width": 1 }),
      svgEl("rect", { x: -19, y: -27, width: 38, height: 3, rx: 1.5, fill: display.on ? "#79d3c5" : "#aabbb7" }),
      svgEl("rect", { x: -19, y: -20, width: 25, height: 2, rx: 1, fill: display.on ? "#dff4ef" : "#bccbc7" })
    );
    svg.append(group);
  } else {
    const holding = arm.state.gripper?.holding;
    const group = svgEl("g", { transform: `translate(${endX} ${endY}) rotate(${angle2 * 57.3 + 90})` });
    group.append(
      svgEl("line", { x1: -9, y1: 0, x2: -9, y2: 18, stroke: "#405a63", "stroke-width": 3, "stroke-linecap": "round" }),
      svgEl("line", { x1: 9, y1: 0, x2: 9, y2: 18, stroke: "#405a63", "stroke-width": 3, "stroke-linecap": "round" }),
      svgEl("circle", { cx: 0, cy: 26, r: holding ? 7 : 3, fill: holding ? "#cf4a29" : "#b9c7c3" })
    );
    svg.append(group);
  }
  return svg;
}

function armStateText(arm) {
  const pose = arm.state.pose || {};
  const lines = [`pose  ${pose.x}, ${pose.y}, ${pose.z}`];
  if (arm.role === "lamp") {
    const light = arm.state.light || {};
    lines.push(light.on ? `light  ${light.brightness}% · ${light.warmth}K` : "light  off");
  } else if (arm.role === "display") {
    const display = arm.state.display || {};
    lines.push(display.on ? `screen ${display.text || "on"}` : "screen off");
  } else {
    const gripper = arm.state.gripper || {};
    if (gripper.holding) lines.push(`holding ${gripper.holding}`);
    if (gripper.last_press) lines.push(`pressed ${gripper.last_press} ×${gripper.press_count || 1}`);
  }
  lines.push(arm.state.estopped ? "ESTOP" : `mode   ${arm.state.mode}`);
  return lines.join("\n");
}

function renderArms() {
  const container = $("#arms");
  container.replaceChildren();
  for (const arm of state.data?.arms || []) {
    const card = document.createElement("article");
    card.className = "arm-card" + (arm.state.estopped ? " is-stopped" : "");
    const role = document.createElement("div");
    role.className = "arm-role";
    role.textContent = `${arm.role} · ${arm.room}`;
    const title = document.createElement("h3");
    title.textContent = arm.name;
    const svg = drawArm(arm);
    const meta = document.createElement("div");
    meta.className = "arm-meta";
    meta.textContent = `${arm.id} · ${arm.state.mode}`;
    const detail = document.createElement("div");
    detail.className = "arm-state";
    detail.textContent = armStateText(arm);
    card.append(role, title, svg, meta, detail);
    container.append(card);
  }
  const estop = Boolean(state.data?.estopped);
  $("#estop-banner").hidden = !estop;
  $("#arm-summary").textContent = estop ? "急停中" : `${state.data?.arms?.length || 0} 条臂 · 正常`;
}

function renderScenes() {
  const container = $("#scenes");
  container.replaceChildren();
  for (const scene of state.data?.scenes || []) {
    const button = document.createElement("button");
    button.className = "scene-button";
    button.type = "button";
    button.dataset.scene = scene.id;
    button.innerHTML = `<strong>${scene.title}</strong><br><span>${scene.description}</span>`;
    button.addEventListener("click", () => runScene(scene.id));
    container.append(button);
  }
}

function renderSteps(result) {
  const container = $("#steps");
  container.replaceChildren();
  $("#scene-title").textContent = result.title || "场景";
  $("#scene-description").textContent = result.description || "";
  for (const step of result.steps || []) {
    const line = document.createElement("div");
    line.className = "step" + (step.ok ? "" : " is-error");
    line.textContent = `${step.ok ? "✓" : "×"} ${step.device_id}.${step.capability}${step.error ? ` · ${step.error}` : ""}`;
    container.append(line);
  }
  const trace = $("#trace");
  for (const step of result.steps || []) {
    const line = document.createElement("div");
    line.className = "trace-line";
    const type = document.createElement("b");
    type.textContent = step.ok ? "robot.step" : "robot.error";
    line.append(type, ` ${step.device_id} · ${step.capability}`);
    if (step.error) line.append(` — ${step.error}`);
    trace.prepend(line);
  }
}

async function refresh() {
  try {
    state.data = await api("/api/robot/state");
    renderArms();
    renderScenes();
  } catch (error) {
    $("#arm-summary").textContent = error.message;
  }
}

async function runScene(scene) {
  if (state.busy) return;
  state.busy = true;
  document.querySelectorAll(".scene-button").forEach((button) => { button.disabled = true; });
  try {
    const result = await api("/api/robot/scene", {
      method: "POST",
      body: JSON.stringify({ scene }),
    });
    state.data = result.state;
    renderArms();
    renderSteps(result);
  } catch (error) {
    $("#scene-title").textContent = "执行失败";
    $("#scene-description").textContent = error.message;
  } finally {
    state.busy = false;
    document.querySelectorAll(".scene-button").forEach((button) => { button.disabled = false; });
  }
}

refresh();
