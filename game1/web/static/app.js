const appState = {
  state: null,
  snapshots: [],
  engines: [],
  selectedSnapshotId: "",
  selectedEngine: "chip",
  graph: null,
  graphSelectedCaseId: "all",
  graphSelectedNodeId: "",
  graphSelectedEdgeId: "",
  tickerId: null,
  tickInFlight: false,
  logs: [],
};

const SVG_NS = "http://www.w3.org/2000/svg";

function logLine(message, level = "info") {
  const ts = new Date().toLocaleTimeString();
  appState.logs.unshift({ ts, level, message });
  appState.logs = appState.logs.slice(0, 80);

  const consoleLog = document.getElementById("console-log");
  consoleLog.innerHTML = "";
  for (const entry of appState.logs) {
    const row = document.createElement("div");
    row.className = `log-entry ${entry.level}`;
    row.textContent = `[${entry.ts}] ${entry.message}`;
    consoleLog.appendChild(row);
  }
}

async function apiRequest(path, options = {}, { quiet = false } = {}) {
  const requestOptions = {
    method: options.method || "GET",
    headers: {},
    cache: "no-store",
  };

  if (options.body !== undefined) {
    requestOptions.headers["Content-Type"] = "application/json; charset=utf-8";
    requestOptions.body = JSON.stringify(options.body);
  }

  const response = await fetch(path, requestOptions);
  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    throw new Error(`HTTP ${response.status}: invalid JSON response`);
  }

  if (!response.ok || !payload.ok) {
    throw new Error(payload.message || `HTTP ${response.status}`);
  }

  if (!quiet && payload.message) {
    logLine(payload.message, "ok");
  }
  return payload.data;
}

function setButtonState() {
  const hasSnapshot = Boolean(appState.selectedSnapshotId);
  document.getElementById("report-snapshot-button").disabled = !hasSnapshot;
  document.getElementById("graph-snapshot-button").disabled = !hasSnapshot;

  const runState = appState.state ? appState.state.run : null;
  const running = Boolean(runState && runState.running);
  document.getElementById("run-start-button").disabled = running;
  document.getElementById("run-stop-button").disabled = !running;
}

function renderHeader(runState) {
  document.getElementById("header-game").textContent = runState.active_game || "-";
  document.getElementById("header-model").textContent = runState.active_model || "-";
  document.getElementById("header-mode").textContent = runState.active_mode || "-";
  document.getElementById("header-run").textContent = runState.running ? "running" : "stopped";
}

function renderMetrics(runState) {
  document.getElementById("metric-status").textContent = runState.running ? "running" : "stopped";
  document.getElementById("metric-accuracy").textContent = `${runState.accuracy}%`;
  document.getElementById("metric-reward").textContent = String(runState.reward);
  document.getElementById("metric-steps").textContent = String(runState.steps);
  document.getElementById("metric-logit").textContent = Number(runState.logit).toFixed(4);
  document.getElementById("metric-prob").textContent = Number(runState.prob).toFixed(4);
}

function renderGames(games) {
  const root = document.getElementById("games-list");
  root.innerHTML = "";
  for (const game of games) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `list-button ${game.active ? "active" : ""}`;
    button.innerHTML = `<strong>${game.title}</strong><span>${game.key}</span><small>${game.summary}</small>`;
    button.addEventListener("click", async () => {
      await performAction(() => apiRequest("/api/games/select", {
        method: "POST",
        body: { key: game.key },
      }));
    });
    root.appendChild(button);
  }
}

function renderModels(models) {
  const root = document.getElementById("models-list");
  root.innerHTML = "";
  for (const model of models) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `list-button ${model.active ? "active" : ""}`;
    button.innerHTML = `<strong>${model.title}</strong><span>${model.key}</span><small>neurons ${model.n_neurons} · steps ${model.steps}</small>`;
    button.addEventListener("click", async () => {
      await performAction(() => apiRequest("/api/models/select", {
        method: "POST",
        body: { key: model.key },
      }));
    });
    root.appendChild(button);
  }
}

function renderModes(modes) {
  const root = document.getElementById("modes-list");
  root.innerHTML = "";
  for (const mode of modes) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `list-button ${mode.active ? "active" : ""}`;
    button.innerHTML = `<strong>${mode.title}</strong><span>${mode.key}</span><small>${mode.summary}</small>`;
    button.addEventListener("click", async () => {
      await performAction(() => apiRequest("/api/modes/select", {
        method: "POST",
        body: { key: mode.key },
      }));
    });
    root.appendChild(button);
  }
}

function renderSnapshots() {
  const root = document.getElementById("snapshots-list");
  root.innerHTML = "";

  if (!appState.snapshots.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No snapshots yet.";
    root.appendChild(empty);
    return;
  }

  for (const snapshot of appState.snapshots) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `list-button ${snapshot.id === appState.selectedSnapshotId ? "active" : ""}`;
    button.innerHTML = `<strong>${snapshot.title}</strong><span>${snapshot.model} · ${snapshot.game}</span><small>${snapshot.mode} · ${snapshot.accuracy}%</small>`;
    button.addEventListener("click", () => {
      appState.selectedSnapshotId = snapshot.id;
      renderSnapshots();
      setButtonState();
    });
    root.appendChild(button);
  }
}

function renderEngines() {
  const root = document.getElementById("engines-list");
  root.innerHTML = "";

  for (const engine of appState.engines) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `list-button ${engine.key === appState.selectedEngine ? "active" : ""}`;
    button.innerHTML = `<strong>${engine.title}</strong><span>${engine.key}</span><small>${engine.summary}</small>`;
    button.addEventListener("click", () => {
      appState.selectedEngine = engine.key;
      renderEngines();
    });
    root.appendChild(button);
  }
}

function renderReport(report) {
  document.getElementById("report-output").textContent = report.body || "Empty report.";
}

function appendGraphField(root, label, value) {
  if (value === undefined || value === null || value === "") {
    return;
  }
  const row = document.createElement("div");
  row.className = "graph-row";

  const term = document.createElement("span");
  term.className = "graph-label";
  term.textContent = label;

  const detail = document.createElement("strong");
  detail.textContent = String(value);

  row.append(term, detail);
  root.appendChild(row);
}

function formatGraphNumber(value, digits = 4) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return value.toFixed(digits);
}

function graphEdgeId(edge) {
  return `${edge.from}->${edge.to}`;
}

function findGraphCase(graph, caseId) {
  if (!graph || !Array.isArray(graph.cases)) {
    return null;
  }
  return graph.cases.find((item) => item.id === caseId) || null;
}

function selectedGraphCase(graph, caseId) {
  if (!graph || caseId === "all") {
    return null;
  }
  return findGraphCase(graph, caseId);
}

function graphNodeById(graph, nodeId) {
  if (!graph || !nodeId) {
    return null;
  }

  const inputNode = (graph.inputs || []).find((item) => item.id === nodeId);
  if (inputNode) {
    return { ...inputNode, kind: "input" };
  }

  const hiddenNode = (graph.hidden || []).find((item) => item.id === nodeId);
  if (hiddenNode) {
    return { ...hiddenNode, kind: "hidden" };
  }

  if (graph.output && graph.output.id === nodeId) {
    return { ...graph.output, kind: "output" };
  }

  return null;
}

function findGraphEdge(graph, edgeId) {
  if (!graph || !edgeId || !Array.isArray(graph.edges)) {
    return null;
  }
  return graph.edges.find((edge) => graphEdgeId(edge) === edgeId) || null;
}

function setGraphSelection({ caseId, nodeId, edgeId }) {
  if (caseId !== undefined) {
    appState.graphSelectedCaseId = caseId;
  }
  if (nodeId !== undefined) {
    appState.graphSelectedNodeId = nodeId;
  }
  if (edgeId !== undefined) {
    appState.graphSelectedEdgeId = edgeId;
  }
  rerenderGraphPanel();
}

function buildGraphChip(label, value) {
  const chip = document.createElement("div");
  chip.className = "graph-chip";

  const term = document.createElement("span");
  term.className = "graph-label";
  term.textContent = label;

  const detail = document.createElement("strong");
  detail.textContent = String(value);

  chip.append(term, detail);
  return chip;
}

function buildGraphSummary(graph) {
  const summary = document.createElement("div");
  summary.className = "graph-summary";
  summary.appendChild(buildGraphChip("Target", graph.target_role || "unknown"));
  summary.appendChild(buildGraphChip("Network", graph.network_role || "unknown"));
  summary.appendChild(buildGraphChip(
    "Match",
    typeof graph.match === "boolean" ? (graph.match ? "yes" : "no") : "n/a",
  ));
  summary.appendChild(buildGraphChip(
    "CMOS",
    typeof graph.cmos_transistors === "number" ? `${graph.cmos_transistors}T` : "n/a",
  ));
  return summary;
}

function renderGraphControls(graph) {
  const toolbar = document.createElement("div");
  toolbar.id = "graph-toolbar";
  toolbar.className = "graph-toolbar";

  const caseIds = ["all", ...(graph.cases || []).map((item) => item.id)];
  for (const caseId of caseIds) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = caseId;
    if (caseId === appState.graphSelectedCaseId) {
      button.classList.add("active");
    }
    button.addEventListener("click", () => {
      setGraphSelection({
        caseId,
        edgeId: "",
      });
    });
    toolbar.appendChild(button);
  }

  return toolbar;
}

function createSvgElement(name, attrs = {}) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function drawNetworkSvg(graph, selectedCaseId) {
  const container = document.createElement("div");
  container.id = "graph-svg";
  container.className = "graph-svg";

  if (graph.status !== "ok") {
    const copy = document.createElement("p");
    copy.className = "graph-message";
    copy.textContent = graph.message || "Graph unsupported for this snapshot.";
    container.appendChild(copy);
    return container;
  }

  const currentCase = selectedGraphCase(graph, selectedCaseId);
  const hiddenCount = Math.max(graph.hidden.length, 1);
  const width = 720;
  const height = Math.max(320, 150 + hiddenCount * 74);
  const top = 70;
  const bottom = 70;
  const hiddenStep = hiddenCount === 1 ? 0 : (height - top - bottom) / (hiddenCount - 1);
  const centerY = height / 2;
  const positions = {
    x0: { x: 110, y: centerY - 56 },
    x1: { x: 110, y: centerY + 56 },
    out: { x: 610, y: centerY },
  };

  graph.hidden.forEach((node, index) => {
    positions[node.id] = {
      x: 360,
      y: hiddenCount === 1 ? centerY : top + hiddenStep * index,
    };
  });

  const svg = createSvgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": "MLP graph inspector",
  });

  svg.appendChild(createSvgElement("rect", {
    x: 0,
    y: 0,
    width,
    height,
    rx: 18,
    ry: 18,
    fill: "#f8fbfe",
  }));

  const title = createSvgElement("text", {
    x: 28,
    y: 34,
    class: "graph-layer-label",
  });
  title.textContent = currentCase
    ? `Case ${currentCase.id}: x0=${currentCase.x0}, x1=${currentCase.x1}, logit=${formatGraphNumber(currentCase.logit)}`
    : "All cases: topology and weights";
  svg.appendChild(title);

  const layerTitles = [
    { label: "inputs", x: positions.x0.x - 24 },
    { label: "hidden", x: positions.h0 ? positions.h0.x - 28 : 332 },
    { label: "output", x: positions.out.x - 28 },
  ];
  for (const item of layerTitles) {
    const text = createSvgElement("text", {
      x: item.x,
      y: 56,
      class: "graph-layer-label",
    });
    text.textContent = item.label;
    svg.appendChild(text);
  }

  for (const edge of graph.edges) {
    const from = positions[edge.from];
    const to = positions[edge.to];
    const edgeId = graphEdgeId(edge);
    const edgeGroup = createSvgElement("g", {});
    const isSelected = appState.graphSelectedEdgeId === edgeId;
    let edgeActive = false;
    if (currentCase) {
      if (edge.from === "x0" || edge.from === "x1") {
        const sourceValue = currentCase[edge.from];
        const targetState = currentCase.hidden ? currentCase.hidden[edge.to] : null;
        edgeActive = sourceValue === 1 && Boolean(targetState && targetState.active);
      } else {
        const sourceState = currentCase.hidden ? currentCase.hidden[edge.from] : null;
        edgeActive = Boolean(sourceState && sourceState.active);
      }
    }

    const line = createSvgElement("line", {
      x1: from.x,
      y1: from.y,
      x2: to.x,
      y2: to.y,
      class: [
        "graph-edge",
        edge.weight >= 0 ? "positive" : "negative",
        edgeActive ? "active" : "",
        isSelected ? "selected" : "",
      ].filter(Boolean).join(" "),
      "stroke-width": 1.5 + Math.min(5.5, Math.abs(edge.weight)),
    });
    line.addEventListener("click", (event) => {
      event.stopPropagation();
      setGraphSelection({
        nodeId: "",
        edgeId,
      });
    });

    const midX = (from.x + to.x) / 2;
    const midY = (from.y + to.y) / 2;
    const weightLabel = createSvgElement("text", {
      x: midX + (edge.from === "x1" ? 10 : -6),
      y: midY + (edge.to === "out" ? -10 : edge.from === "x1" ? 14 : -10),
      class: `graph-weight ${edge.weight >= 0 ? "positive" : "negative"} ${isSelected ? "selected" : ""}`,
    });
    weightLabel.textContent = formatGraphNumber(edge.weight);
    weightLabel.addEventListener("click", (event) => {
      event.stopPropagation();
      setGraphSelection({
        nodeId: "",
        edgeId,
      });
    });

    edgeGroup.append(line, weightLabel);
    svg.appendChild(edgeGroup);
  }

  const allNodes = [
    ...(graph.inputs || []).map((node) => ({ ...node, kind: "input" })),
    ...(graph.hidden || []).map((node) => ({ ...node, kind: "hidden" })),
    { ...graph.output, kind: "output" },
  ];

  for (const node of allNodes) {
    const position = positions[node.id];
    const group = createSvgElement("g", {
      class: [
        "graph-node",
        node.id === appState.graphSelectedNodeId ? "selected" : "",
        node.dead ? "dead" : "",
      ].filter(Boolean).join(" "),
    });

    let isActive = false;
    if (currentCase) {
      if (node.id === "x0" || node.id === "x1") {
        isActive = currentCase[node.id] === 1;
      } else if (node.kind === "hidden") {
        const state = currentCase.hidden ? currentCase.hidden[node.id] : null;
        isActive = Boolean(state && state.active);
      } else if (node.id === "out") {
        isActive = currentCase.network === 1;
      }
    }
    if (isActive) {
      group.classList.add("active");
    }

    const shapeAttrs = {
      cx: position.x,
      cy: position.y,
      r: node.kind === "hidden" ? 24 : 22,
    };
    group.appendChild(createSvgElement("circle", shapeAttrs));

    const label = createSvgElement("text", {
      x: position.x,
      y: position.y + 5,
      "text-anchor": "middle",
      class: "graph-node-label",
    });
    label.textContent = node.label;
    group.appendChild(label);

    const sideText = createSvgElement("text", {
      x: position.x + (node.kind === "output" ? 34 : 36),
      y: position.y - 8,
      class: "graph-node-copy",
    });
    if (node.kind === "hidden") {
      const hiddenState = currentCase && currentCase.hidden ? currentCase.hidden[node.id] : null;
      sideText.textContent = hiddenState
        ? `${node.role || "unknown"} · a=${formatGraphNumber(hiddenState.activation)}`
        : (node.role || "unknown");
    } else if (node.kind === "output") {
      sideText.textContent = currentCase
        ? `y=${currentCase.network} · logit=${formatGraphNumber(currentCase.logit)}`
        : `bias=${formatGraphNumber(node.bias)}`;
    } else {
      sideText.textContent = currentCase ? `value=${currentCase[node.id]}` : node.label;
    }
    group.appendChild(sideText);

    if (node.kind === "hidden" && currentCase && currentCase.hidden) {
      const hiddenState = currentCase.hidden[node.id];
      if (hiddenState) {
        const detail = createSvgElement("text", {
          x: position.x + 36,
          y: position.y + 14,
          class: "graph-node-copy subtle",
        });
        detail.textContent = `z=${formatGraphNumber(hiddenState.z)}`;
        group.appendChild(detail);
      }
    }

    group.addEventListener("click", (event) => {
      event.stopPropagation();
      setGraphSelection({
        nodeId: node.id,
        edgeId: "",
      });
    });
    svg.appendChild(group);
  }

  svg.addEventListener("click", () => {
    setGraphSelection({
      edgeId: "",
    });
  });

  container.appendChild(svg);
  return container;
}

function renderGraphDetails(graph, selectedNodeId, selectedCaseId) {
  const details = document.createElement("div");
  details.id = "graph-details";
  details.className = "graph-details";
  details.appendChild(buildGraphSummary(graph));

  const caseCopy = document.createElement("p");
  caseCopy.className = "graph-copy";
  if (graph.status !== "ok") {
    caseCopy.textContent = "Inspector is unavailable for this snapshot.";
    details.appendChild(caseCopy);
    const message = document.createElement("p");
    message.className = "graph-message";
    message.textContent = graph.message || "Graph unsupported for this snapshot.";
    details.appendChild(message);
    return details;
  }

  const currentCase = selectedGraphCase(graph, selectedCaseId);
  caseCopy.textContent = currentCase
    ? `Selected case ${currentCase.id}: target ${currentCase.target ?? "?"}, network ${currentCase.network}, logit ${formatGraphNumber(currentCase.logit)}.`
    : "Selected case: all. Click 00/01/10/11 to inspect hidden activations.";
  details.appendChild(caseCopy);

  const info = document.createElement("div");
  info.className = "graph-inspector";

  const selectedEdge = findGraphEdge(graph, appState.graphSelectedEdgeId);
  if (selectedEdge) {
    appendGraphField(info, "Edge", `${selectedEdge.from} → ${selectedEdge.to}`);
    appendGraphField(info, "Weight", formatGraphNumber(selectedEdge.weight));
    appendGraphField(info, "Sign", selectedEdge.weight >= 0 ? "positive" : "negative");
    details.appendChild(info);
    return details;
  }

  const node = graphNodeById(graph, selectedNodeId);
  if (!node) {
    const hint = document.createElement("p");
    hint.className = "graph-copy";
    hint.textContent = "Click a node or edge to inspect it.";
    details.appendChild(hint);
    return details;
  }

  appendGraphField(info, "Node", node.label || node.id);
  appendGraphField(info, "Type", node.kind);

  if (node.kind === "input") {
    appendGraphField(info, "Value", currentCase ? currentCase[node.id] : "all cases");
  }

  if (node.kind === "hidden") {
    appendGraphField(info, "Role", node.role || "unknown");
    appendGraphField(info, "Bias", formatGraphNumber(node.bias));
    appendGraphField(info, "Output weight", formatGraphNumber(node.output_weight));
    appendGraphField(info, "Dead", node.dead ? "yes" : "no");

    const incoming = (graph.edges || []).filter((edge) => edge.to === node.id);
    for (const edge of incoming) {
      appendGraphField(info, `${edge.from} → ${node.id}`, formatGraphNumber(edge.weight));
    }

    const hiddenState = currentCase && currentCase.hidden ? currentCase.hidden[node.id] : null;
    if (hiddenState) {
      appendGraphField(info, "z", formatGraphNumber(hiddenState.z));
      appendGraphField(info, "activation", formatGraphNumber(hiddenState.activation));
      appendGraphField(info, "Active", hiddenState.active ? "yes" : "no");
    }
  }

  if (node.kind === "output") {
    appendGraphField(info, "Bias", formatGraphNumber(node.bias));
    const incoming = (graph.edges || []).filter((edge) => edge.to === node.id);
    for (const edge of incoming) {
      appendGraphField(info, `${edge.from} → out`, formatGraphNumber(edge.weight));
    }
    if (currentCase) {
      appendGraphField(info, "Logit", formatGraphNumber(currentCase.logit));
      appendGraphField(info, "Network", currentCase.network);
      appendGraphField(info, "Target", currentCase.target ?? "?");
    }
  }

  details.appendChild(info);
  return details;
}

function renderTruthTable(graph, selectedCaseId) {
  const wrap = document.createElement("div");
  wrap.id = "graph-truth-table";
  wrap.className = "graph-table";

  if (graph.status !== "ok") {
    return wrap;
  }

  const title = document.createElement("h3");
  title.textContent = "Truth table";
  wrap.appendChild(title);

  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  for (const label of ["case", "x0", "x1", "target", "network", "logit", "ok"]) {
    const th = document.createElement("th");
    th.textContent = label;
    headerRow.appendChild(th);
  }
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const row of graph.cases || []) {
    const tr = document.createElement("tr");
    if (row.id === selectedCaseId) {
      tr.classList.add("selected");
    }
    tr.addEventListener("click", () => {
      setGraphSelection({ caseId: row.id, edgeId: "" });
    });

    const ok = row.target === null || row.target === undefined ? "-" : (row.target === row.network ? "yes" : "no");
    const values = [
      row.id,
      row.x0,
      row.x1,
      row.target ?? "?",
      row.network,
      formatGraphNumber(row.logit),
      ok,
    ];
    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = String(value);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }

  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function rerenderGraphPanel() {
  const root = document.getElementById("graph-output");
  root.innerHTML = "";

  if (!appState.graph || !appState.graph.graph) {
    root.textContent = "Load current or snapshot graph.";
    return;
  }

  const { graph } = appState.graph;
  root.appendChild(renderGraphDetails(
    graph,
    appState.graphSelectedNodeId,
    appState.graphSelectedCaseId,
  ));

  if (graph.status === "ok") {
    root.appendChild(renderGraphControls(graph));
  }

  root.appendChild(drawNetworkSvg(graph, appState.graphSelectedCaseId));

  if (graph.status === "ok") {
    root.appendChild(renderTruthTable(graph, appState.graphSelectedCaseId));
  }
}

function renderGraph(payload) {
  appState.graph = payload;
  if (!payload || !payload.graph) {
    appState.graphSelectedCaseId = "all";
    appState.graphSelectedNodeId = "";
    appState.graphSelectedEdgeId = "";
    rerenderGraphPanel();
    return;
  }

  appState.graphSelectedCaseId = "all";
  appState.graphSelectedEdgeId = "";
  appState.graphSelectedNodeId = payload.graph.output && payload.graph.output.id
    ? payload.graph.output.id
    : "";
  rerenderGraphPanel();
}

function applyState(state) {
  appState.state = state;
  renderHeader(state.run);
  renderMetrics(state.run);
  renderGames(state.games);
  renderModels(state.models);
  renderModes(state.modes);
  syncTicker(state.run.running);
  setButtonState();
}

function syncTicker(running) {
  if (running && !appState.tickerId) {
    appState.tickerId = window.setInterval(() => {
      void tickRun();
    }, 200);
    return;
  }
  if (!running && appState.tickerId) {
    window.clearInterval(appState.tickerId);
    appState.tickerId = null;
  }
}

async function refreshState() {
  const data = await apiRequest("/api/state", {}, { quiet: true });
  applyState(data.state);
}

async function refreshSnapshots() {
  const data = await apiRequest("/api/snapshots", {}, { quiet: true });
  appState.snapshots = data.snapshots;
  if (
    appState.selectedSnapshotId &&
    !appState.snapshots.some((item) => item.id === appState.selectedSnapshotId)
  ) {
    appState.selectedSnapshotId = "";
  }
  renderSnapshots();
  setButtonState();
}

async function refreshEngines() {
  const data = await apiRequest("/api/lab/engines", {}, { quiet: true });
  appState.engines = data.engines;
  if (!appState.engines.some((item) => item.key === appState.selectedEngine) && appState.engines.length) {
    appState.selectedEngine = appState.engines[0].key;
  }
  renderEngines();
}

async function refreshAll() {
  await Promise.all([refreshState(), refreshSnapshots(), refreshEngines()]);
}

async function tickRun() {
  if (appState.tickInFlight || !appState.state || !appState.state.run.running) {
    return;
  }

  appState.tickInFlight = true;
  try {
    const data = await apiRequest("/api/run/tick", { method: "POST" }, { quiet: true });
    appState.state.run = data.run;
    renderHeader(data.run);
    renderMetrics(data.run);
    setButtonState();
  } catch (error) {
    logLine(error.message, "error");
    syncTicker(false);
  } finally {
    appState.tickInFlight = false;
  }
}

async function performAction(action) {
  try {
    await action();
    await refreshState();
  } catch (error) {
    logLine(error.message, "error");
  }
}

async function openCurrentReport() {
  try {
    const data = await apiRequest("/api/lab/report-current", {
      method: "POST",
      body: { engine: appState.selectedEngine },
    });
    renderReport(data.report);
  } catch (error) {
    logLine(error.message, "error");
  }
}

async function openSnapshotReport() {
  if (!appState.selectedSnapshotId) {
    logLine("Select a snapshot first.", "warn");
    return;
  }

  try {
    const params = new URLSearchParams({
      snapshot: appState.selectedSnapshotId,
      engine: appState.selectedEngine,
    });
    const data = await apiRequest(`/api/lab/report?${params.toString()}`, {}, { quiet: true });
    renderReport(data.report);
    logLine(`Loaded report for snapshot ${appState.selectedSnapshotId}.`, "ok");
  } catch (error) {
    logLine(error.message, "error");
  }
}

async function openCurrentGraph() {
  try {
    const data = await apiRequest("/api/graph-current", {
      method: "POST",
    });
    renderGraph(data);
    logLine("Loaded graph inspector for current snapshot.", "ok");
  } catch (error) {
    logLine(error.message, "error");
  }
}

async function openSnapshotGraph() {
  if (!appState.selectedSnapshotId) {
    logLine("Select a snapshot first.", "warn");
    return;
  }

  try {
    const params = new URLSearchParams({ snapshot: appState.selectedSnapshotId });
    const data = await apiRequest(`/api/graph?${params.toString()}`, {}, { quiet: true });
    renderGraph(data);
    logLine(`Loaded graph inspector for snapshot ${appState.selectedSnapshotId}.`, "ok");
  } catch (error) {
    logLine(error.message, "error");
  }
}

function bindUi() {
  document.getElementById("run-start-button").addEventListener("click", async () => {
    await performAction(() => apiRequest("/api/run/start", { method: "POST" }));
  });

  document.getElementById("run-stop-button").addEventListener("click", async () => {
    await performAction(() => apiRequest("/api/run/stop", { method: "POST" }));
  });

  for (const button of document.querySelectorAll(".step-button")) {
    button.addEventListener("click", async () => {
      const steps = Number(button.dataset.steps);
      await performAction(() => apiRequest("/api/run/steps", {
        method: "POST",
        body: { steps },
      }));
    });
  }

  document.getElementById("save-snapshot-button").addEventListener("click", async () => {
    try {
      await apiRequest("/api/snapshots/save", { method: "POST" });
      await refreshSnapshots();
      await refreshState();
    } catch (error) {
      logLine(error.message, "error");
    }
  });

  document.getElementById("reset-model-button").addEventListener("click", async () => {
    const activeModel = appState.state && appState.state.run ? appState.state.run.active_model : "";
    if (!activeModel) {
      logLine("No active model to reset.", "warn");
      return;
    }
    await performAction(() => apiRequest("/api/models/reset", {
      method: "POST",
      body: { key: activeModel },
    }));
  });

  document.getElementById("report-current-button").addEventListener("click", async () => {
    await openCurrentReport();
  });

  document.getElementById("report-snapshot-button").addEventListener("click", async () => {
    await openSnapshotReport();
  });

  document.getElementById("graph-current-button").addEventListener("click", async () => {
    await openCurrentGraph();
  });

  document.getElementById("graph-snapshot-button").addEventListener("click", async () => {
    await openSnapshotGraph();
  });
}

async function init() {
  bindUi();
  renderGraph(null);
  try {
    await refreshAll();
    logLine("Web UI ready.", "ok");
  } catch (error) {
    logLine(error.message, "error");
  }
}

window.addEventListener("load", () => {
  void init();
});
