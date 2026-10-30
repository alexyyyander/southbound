const state = {
  endpoints: [],
  sessionId: sessionStorage.getItem("southbound-session") || "",
  token: sessionStorage.getItem("southbound-token") || "",
  inputEndpoint: "",
  sending: false,
  handoffPending: false,
};

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, {
    headers,
    ...options,
  });
  const raw = await response.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    data = {};
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    data = {};
  }
  if (!response.ok) {
    throw new Error(
      data.error || data.message || raw || response.statusText || `HTTP ${response.status}`,
    );
  }
  return data;
}

function endpointById(id) {
  return state.endpoints.find((endpoint) => endpoint.endpoint_id === id);
}

function setStatus(text) {
  $("#status").textContent = text;
}

function renderEndpoints(selectedInput = "") {
  const container = $("#endpoints");
  container.replaceChildren();
  for (const endpoint of state.endpoints) {
    const card = document.createElement("div");
    card.className = "endpoint" + (endpoint.endpoint_id === selectedInput ? " is-selected" : "");
    const title = document.createElement("h3");
    title.textContent = endpoint.name;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${endpoint.kind} · ${endpoint.location || "未分区"}`;
    const tags = document.createElement("div");
    tags.className = "tags";
    for (const [direction, modalities] of [
      ["in", endpoint.input],
      ["out", endpoint.output],
    ]) {
      for (const modality of modalities) {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = `${direction} · ${modality}`;
        tags.append(tag);
      }
    }
    const trust = document.createElement("div");
    trust.className = "trust";
    trust.textContent = endpoint.trust === "owner" ? "owner approved" : endpoint.trust;
    card.append(title, meta, tags, trust);
    container.append(card);
  }
}

function renderControls() {
  const input = $("#input-endpoint");
  const previous = state.inputEndpoint || input.value || "mac-dashboard";
  const previousOutputs = new Set(
    [...document.querySelectorAll("#output-endpoints input:checked")].map(
      (item) => item.value,
    ),
  );
  const hadOutputList = Boolean(
    document.querySelector("#output-endpoints input"),
  );
  input.replaceChildren();
  for (const endpoint of state.endpoints) {
    if (endpoint.trust !== "owner" || !endpoint.input.includes("text")) continue;
    const option = document.createElement("option");
    option.value = endpoint.endpoint_id;
    option.textContent = `${endpoint.name} (${endpoint.kind})`;
    input.append(option);
  }
  if ([...input.options].some((option) => option.value === previous)) {
    input.value = previous;
  } else {
    state.inputEndpoint = input.value;
  }
  if (!state.inputEndpoint) state.inputEndpoint = input.value;

  const outputs = $("#output-endpoints");
  outputs.replaceChildren();
  for (const endpoint of state.endpoints) {
    if (endpoint.trust !== "owner" || !endpoint.output.length) continue;
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = endpoint.endpoint_id;
    checkbox.checked = hadOutputList
      ? previousOutputs.has(endpoint.endpoint_id)
      : ["pi-living", "tv-living"].includes(endpoint.endpoint_id);
    const text = document.createElement("span");
    text.textContent = endpoint.name;
    label.append(checkbox, text);
    outputs.append(label);
  }
  input.onchange = async () => {
    const target = input.value;
    const previousEndpoint = state.inputEndpoint;
    renderEndpoints(target);
    if (!state.sessionId) {
      state.inputEndpoint = target;
      return;
    }
    if (state.handoffPending) {
      input.value = previousEndpoint;
      return;
    }
    state.handoffPending = true;
    input.disabled = true;
    try {
      const result = await api("/api/interaction/handoff", {
        method: "POST",
        body: JSON.stringify({
          session_id: state.sessionId,
          endpoint_id: target,
        }),
      });
      if (result.session) {
        state.inputEndpoint = result.session.input_endpoint;
        setStatus(`已交接给 ${input.options[input.selectedIndex]?.textContent || target}`);
        renderHandoff();
      }
    } catch (error) {
      input.value = previousEndpoint;
      renderEndpoints(previousEndpoint);
      setStatus(error.message);
    } finally {
      input.disabled = false;
      state.handoffPending = false;
    }
  };
  renderEndpoints(input.value);
}

function renderOutputs(outputs) {
  const container = $("#outputs");
  container.replaceChildren();
  if (!outputs.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "这一轮没有输出端点。";
    container.append(empty);
    return;
  }
  for (const output of outputs) {
    const card = document.createElement("article");
    card.className = "output";
    const title = document.createElement("h3");
    title.textContent = endpointById(output.endpoint_id)?.name || output.endpoint_id;
    const modalities = document.createElement("div");
    modalities.className = "modalities";
    modalities.textContent = output.modalities.join(" · ");
    const text = document.createElement("p");
    text.textContent = output.payload.display || output.payload.speech || output.payload.text || output.payload.led || "";
    card.append(title, modalities, text);
    container.append(card);
  }
}

function renderTrace(events) {
  const container = $("#trace");
  container.replaceChildren();
  for (const event of events.slice(-12)) {
    const line = document.createElement("div");
    line.className = "trace-line";
    const type = document.createElement("b");
    type.textContent = event.type;
    line.append(type, ` ${event.endpoint_id || event.session_id || ""}`);
    if (event.text) line.append(` — ${event.text}`);
    container.append(line);
  }
  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "发送一轮后，事件会出现在这里。";
    container.append(empty);
  }
}

function renderHandoff() {
  const container = $("#handoff");
  container.replaceChildren();
  if (!state.sessionId) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "发送第一轮后会出现可交接的端点。";
    container.append(empty);
    return;
  }
  for (const endpoint of state.endpoints) {
    if (endpoint.trust !== "owner" || !endpoint.input.includes("text")) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `切到 ${endpoint.name}`;
    button.onclick = async () => {
      if (state.handoffPending) return;
      state.handoffPending = true;
      button.disabled = true;
      try {
        const result = await api("/api/interaction/handoff", {
          method: "POST",
          body: JSON.stringify({
            session_id: state.sessionId,
            endpoint_id: endpoint.endpoint_id,
          }),
        });
        if (result.session) {
          state.inputEndpoint = result.session.input_endpoint;
          $("#input-endpoint").value = endpoint.endpoint_id;
          setStatus(`已交接给 ${endpoint.name}`);
          $("#session-badge").textContent = "active";
          $("#session-badge").classList.add("live");
          renderEndpoints(endpoint.endpoint_id);
          renderHandoff();
        }
      } catch (error) {
        setStatus(error.message);
      } finally {
        button.disabled = false;
        state.handoffPending = false;
      }
    };
    container.append(button);
  }
}

async function refresh() {
  try {
    const payload = await api("/api/interaction/endpoints");
    state.endpoints = payload.endpoints || [];
    renderControls();
    renderHandoff();
  } catch (error) {
    setStatus(error.message);
  }
}

async function sendTurn(event) {
  event.preventDefault();
  if (state.sending || state.handoffPending) return;
  const text = $("#text").value.trim();
  if (!text) {
    setStatus("先输入一句话。");
    return;
  }
  const outputs = [...document.querySelectorAll("#output-endpoints input:checked")]
    .map((input) => input.value);
  const body = {
    endpoint_id: $("#input-endpoint").value,
    text,
    session_id: state.sessionId,
    execute: $("#execute").checked,
  };
  if (!$("#auto-route").checked) body.output_endpoints = outputs;
  state.sending = true;
  setStatus("发送中…");
  try {
    const result = await api("/api/interaction/input", {
      method: "POST",
      body: JSON.stringify(body),
    });
    state.sessionId = result.session.session_id;
    state.inputEndpoint = result.session.input_endpoint;
    sessionStorage.setItem("southbound-session", state.sessionId);
    $("#session-id").textContent = state.sessionId;
    $("#session-badge").textContent = "active";
    $("#session-badge").classList.add("live");
    renderOutputs(result.outputs);
    renderHandoff();
    setStatus(result.ok ? "完成" : "执行有问题");
    try {
      const events = await api("/api/events/recent?limit=50");
      renderTrace(
        (events.events || []).filter(
          (event) => !state.sessionId || event.session_id === state.sessionId,
        ),
      );
    } catch {
      // A trace refresh must not turn a completed turn into a failed one.
    }
  } catch (error) {
    setStatus(error.message);
  } finally {
    state.sending = false;
  }
}

function clearSession() {
  state.sessionId = "";
  state.inputEndpoint = "";
  sessionStorage.removeItem("southbound-session");
  $("#session-id").textContent = "—";
  $("#session-badge").textContent = "未开始";
  $("#session-badge").classList.remove("live");
  $("#text").value = "";
  renderOutputs([]);
  renderTrace([]);
  renderHandoff();
}

async function restoreSession() {
  if (!state.sessionId) return;
  try {
    const session = await api(
      `/api/interaction/sessions/${encodeURIComponent(state.sessionId)}`,
    );
    state.sessionId = session.session_id;
    state.inputEndpoint = session.input_endpoint || "";
    $("#session-id").textContent = state.sessionId;
    $("#session-badge").textContent = "active";
    $("#session-badge").classList.add("live");
    if (state.inputEndpoint) {
      $("#input-endpoint").value = state.inputEndpoint;
      renderEndpoints(state.inputEndpoint);
    }
    renderHandoff();
  } catch {
    clearSession();
  }
}

$("#composer").addEventListener("submit", sendTurn);
$("#refresh").addEventListener("click", refresh);
$("#new-session").addEventListener("click", clearSession);
$("#runtime-token").value = state.token;
$("#runtime-token").addEventListener("change", (event) => {
  state.token = event.target.value.trim();
  if (state.token) sessionStorage.setItem("southbound-token", state.token);
  else sessionStorage.removeItem("southbound-token");
  refresh().then(restoreSession);
});
if (state.sessionId) {
  $("#session-id").textContent = state.sessionId;
  $("#session-badge").textContent = "active";
  $("#session-badge").classList.add("live");
}
refresh().then(restoreSession);
renderHandoff();
