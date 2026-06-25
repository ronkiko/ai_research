const appState = {
  state: null,
  snapshots: [],
  engines: [],
  selectedSnapshotId: "",
  selectedEngine: "chip",
  activeView: "dashboard",
  report: null,
  reportContext: null,
  graph: null,
  graphSelectedCaseId: "all",
  graphSelectedNodeId: "",
  graphSelectedEdgeId: "",
  tickerId: null,
  tickInFlight: false,
  logs: [],
};

const SVG_NS = "http://www.w3.org/2000/svg";

const VIEW_META = {
  dashboard: {
    title: "Dashboard",
    subtitle: "Fast controls, current metrics, and immediate analysis.",
  },
  run: {
    title: "Run",
    subtitle: "Select the game, model, and mode, then control the active run.",
  },
  lab: {
    title: "Lab",
    subtitle: "Open CHIP, forensic, and prune reports for current or saved snapshots.",
  },
  graph: {
    title: "Graph",
    subtitle: "Inspect the current or saved MLP graph with case-by-case activation details.",
  },
  snapshots: {
    title: "Snapshots",
    subtitle: "Save, select, and reopen recent run states without leaving the operator panel.",
  },
  info: {
    title: "Info",
    subtitle: "Compact architecture notes, operator flow hints, and graph support scope.",
  },
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const map = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return map[char] || char;
  });
}

function formatNumber(value, digits = 4) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "-";
  }
  return number.toFixed(digits);
}

function formatPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "-";
  }
  return `${number}%`;
}

function formatTimestamp(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return "-";
  }
  return new Date(seconds * 1000).toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function runState() {
  return appState.state ? appState.state.run : {
    running: false,
    reward: 0,
    steps: 0,
    accuracy: 0,
    active_game: "",
    active_model: "",
    active_mode: "",
    logit: 0,
    prob: 0.5,
  };
}

function activeItem(items) {
  return (items || []).find((item) => item.active) || null;
}

function activeGame() {
  return activeItem(appState.state ? appState.state.games : []);
}

function activeModel() {
  return activeItem(appState.state ? appState.state.models : []);
}

function activeMode() {
  return activeItem(appState.state ? appState.state.modes : []);
}

function selectedSnapshot() {
  return appState.snapshots.find((item) => item.id === appState.selectedSnapshotId) || null;
}

function graphPayload() {
  return appState.graph && appState.graph.graph ? appState.graph : null;
}

function statusClassForRun(running) {
  return running ? "running" : "stopped";
}

function currentViewMeta() {
  return VIEW_META[appState.activeView] || VIEW_META.dashboard;
}

function logLine(message, level = "info") {
  const timestamp = new Date().toLocaleTimeString();
  appState.logs.unshift({ timestamp, level, message });
  appState.logs = appState.logs.slice(0, 80);
  renderActivityPanels();
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

function setView(viewName) {
  if (!VIEW_META[viewName]) {
    return;
  }
  appState.activeView = viewName;
  renderShell();
}

function renderShell() {
  const meta = currentViewMeta();
  document.getElementById("topbar-title").textContent = meta.title;
  document.getElementById("topbar-subtitle").textContent = meta.subtitle;

  for (const section of document.querySelectorAll(".view")) {
    section.classList.toggle("active", section.id === `view-${appState.activeView}`);
  }

  for (const button of document.querySelectorAll(".nav-button[data-view]")) {
    button.classList.toggle("active", button.dataset.view === appState.activeView);
  }

  const run = runState();
  const metaRoot = document.getElementById("topbar-meta");
  metaRoot.innerHTML = `
    <span class="meta-pill">Game: ${escapeHtml(run.active_game || "-")}</span>
    <span class="meta-pill">Model: ${escapeHtml(run.active_model || "-")}</span>
    <span class="meta-pill">Mode: ${escapeHtml(run.active_mode || "-")}</span>
    <span class="meta-pill status-pill ${statusClassForRun(run.running)}">Run: ${run.running ? "running" : "stopped"}</span>
  `;

  const serverRunStatus = document.getElementById("server-run-status");
  serverRunStatus.textContent = run.running ? "running" : "stopped";
  serverRunStatus.className = `sidebar-pill ${statusClassForRun(run.running)}`;
}

function metricCard(label, value, copy = "") {
  return `
    <div class="metric-card">
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value">${escapeHtml(value)}</div>
      ${copy ? `<p class="summary-copy">${escapeHtml(copy)}</p>` : ""}
    </div>
  `;
}

function statusCard(label, value, copy = "") {
  return `
    <div class="stat-card">
      <div class="stat-label">${escapeHtml(label)}</div>
      <div class="stat-value">${escapeHtml(value)}</div>
      ${copy ? `<p class="summary-copy">${escapeHtml(copy)}</p>` : ""}
    </div>
  `;
}

function summaryCard(label, value, copy = "") {
  return `
    <div class="summary-card">
      <div class="summary-label">${escapeHtml(label)}</div>
      <div class="summary-value">${escapeHtml(value)}</div>
      ${copy ? `<p class="summary-copy">${escapeHtml(copy)}</p>` : ""}
    </div>
  `;
}

function buildMetricsGrid() {
  const run = runState();
  return `
    <div class="metrics-grid">
      ${metricCard("Accuracy", formatPercent(run.accuracy), "Latest run accuracy.")}
      ${metricCard("Reward", String(run.reward), "Reward accumulated in this session.")}
      ${metricCard("Steps", String(run.steps), "Model steps on the active run.")}
      ${metricCard("Logit", formatNumber(run.logit), "Current output logit.")}
      ${metricCard("Prob", formatNumber(run.prob), "Current output probability.")}
    </div>
  `;
}

function buildStatusGrid() {
  const run = runState();
  const game = activeGame();
  const model = activeModel();
  const mode = activeMode();
  return `
    <div class="status-grid">
      ${statusCard("Active game", game ? game.title : "-", game ? game.key : "Select a table.")}
      ${statusCard("Active model", model ? model.title : "-", model ? `${model.key} · ${model.n_neurons} neurons` : "Select a model.")}
      ${statusCard("Active mode", mode ? mode.title : "-", mode ? mode.key : "Select a mode.")}
      ${statusCard("Run status", run.running ? "Running" : "Stopped", run.running ? "Ticker is advancing." : "Ready for manual control.")}
    </div>
  `;
}

function buildChoiceButtons(items, type) {
  if (!items || !items.length) {
    return `<div class="empty-state"><p class="empty-copy">No ${escapeHtml(type)} available.</p></div>`;
  }

  return `
    <div class="choice-list compact">
      ${items.map((item) => {
        let copy = item.summary || "";
        let tags = "";
        if (type === "models") {
          tags = `
            <div class="metric-inline">
              <span class="metric-tag">${escapeHtml(`${item.n_neurons} neurons`)}</span>
              <span class="metric-tag">${escapeHtml(`${item.n_params} params`)}</span>
              <span class="metric-tag">${escapeHtml(`${item.steps} steps`)}</span>
            </div>
          `;
        } else if (type === "modes") {
          copy = item.summary || item.help || "";
        }

        const dataAttr = type === "games"
          ? `data-select-game="${escapeHtml(item.key)}"`
          : type === "models"
            ? `data-select-model="${escapeHtml(item.key)}"`
            : `data-select-mode="${escapeHtml(item.key)}"`;

        return `
          <button
            type="button"
            class="choice-button ${item.active ? "active" : ""}"
            ${dataAttr}
            title="${escapeHtml(copy || item.key)}"
          >
            <div class="choice-topline">
              <div>
                <h3 class="choice-title">${escapeHtml(item.title)}</h3>
                <div class="choice-key">${escapeHtml(item.key)}</div>
              </div>
              ${item.active ? '<span class="card-pill">active</span>' : ""}
            </div>
            ${tags}
            ${copy ? `<p class="choice-copy">${escapeHtml(copy)}</p>` : ""}
          </button>
        `;
      }).join("")}
    </div>
  `;
}

function buildEngineButtons() {
  if (!appState.engines.length) {
    return `<div class="empty-state"><p class="empty-copy">No lab engines registered.</p></div>`;
  }
  return `
    <div class="engine-row">
      ${appState.engines.map((engine) => `
        <button
          type="button"
          class="engine-button ${engine.key === appState.selectedEngine ? "active" : ""}"
          data-select-engine="${escapeHtml(engine.key)}"
          title="${escapeHtml(engine.summary)}"
        >${escapeHtml(engine.title)}</button>
      `).join("")}
    </div>
  `;
}

function buildRecentSnapshotCards(limit = 5) {
  const recent = appState.snapshots.slice(0, limit);
  if (!recent.length) {
    return `<div class="empty-state"><p class="empty-copy">No snapshots yet. Save one after a run or step batch.</p></div>`;
  }

  return `
    <div class="recent-list">
      ${recent.map((snapshot) => `
        <div class="recent-item ${snapshot.id === appState.selectedSnapshotId ? "selected" : ""}">
          <div class="snapshot-head">
            <div>
              <h3 class="snapshot-title">${escapeHtml(snapshot.title)}</h3>
              <div class="snapshot-meta">
                <span class="table-pill">${escapeHtml(snapshot.model)}</span>
                <span class="table-pill">${escapeHtml(snapshot.game)}</span>
                <span class="table-pill">${escapeHtml(snapshot.mode)}</span>
                <span class="table-pill">acc ${escapeHtml(snapshot.accuracy)}%</span>
              </div>
            </div>
            <button type="button" class="ghost-button" data-snapshot-select="${escapeHtml(snapshot.id)}">Select</button>
          </div>
          <div class="snapshot-path">${escapeHtml(formatTimestamp(snapshot.mtime))} · ${escapeHtml(snapshot.path)}</div>
          <div class="inline-actions">
            <button type="button" class="mini-button" data-snapshot-report="${escapeHtml(snapshot.id)}">Open report</button>
            <button type="button" class="mini-button" data-snapshot-graph="${escapeHtml(snapshot.id)}">Open graph</button>
          </div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderDashboard() {
  const root = document.getElementById("dashboard-content");
  root.innerHTML = `
    <div class="stack-layout">
      <section class="card accent-card">
        <div class="card-header compact">
          <div>
            <h2 class="card-title">Status</h2>
            <p class="card-subtitle">Current game, model, mode, and run state at a glance.</p>
          </div>
        </div>
        ${buildStatusGrid()}
      </section>

      <section class="card">
        <div class="card-header compact">
          <div>
            <h2 class="card-title">Metrics</h2>
            <p class="card-subtitle">The operator does not need to scroll elsewhere after a run or step batch.</p>
          </div>
        </div>
        ${buildMetricsGrid()}
      </section>

      <div class="two-column">
        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Primary actions</h2>
              <p class="card-subtitle">Run now, step forward, or save the current state.</p>
            </div>
          </div>
          <div class="action-stack">
            <div class="action-group">
              <h3 class="action-group-title">Run</h3>
              <div class="action-row">
                <button type="button" class="action-button primary" data-action="run-start" ${runState().running ? "disabled" : ""}>Start</button>
                <button type="button" class="action-button stop" data-action="run-stop" ${runState().running ? "" : "disabled"}>Stop</button>
                <button type="button" class="action-button secondary" data-run-steps="10">Step 10</button>
                <button type="button" class="action-button secondary" data-run-steps="100">Step 100</button>
                <button type="button" class="action-button secondary" data-run-steps="1000">Step 1000</button>
              </div>
            </div>
            <div class="action-group">
              <h3 class="action-group-title">Snapshot</h3>
              <div class="action-row">
                <button type="button" class="action-button subtle" data-action="save-snapshot">Save snapshot</button>
              </div>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Analyze current</h2>
              <p class="card-subtitle">Open the current state directly in the lab or graph inspector.</p>
            </div>
          </div>
          <div class="action-stack">
            <div class="action-group">
              <h3 class="action-group-title">Analyze current</h3>
              <div class="action-row">
                <button type="button" class="chip-button" data-analyze-current="chip">Chip</button>
                <button type="button" class="chip-button" data-analyze-current="forensic">Forensic</button>
                <button type="button" class="chip-button" data-analyze-current="prune">Prune</button>
                <button type="button" class="chip-button" data-action="graph-current">Graph</button>
              </div>
            </div>
            <div class="action-group">
              <h3 class="action-group-title">Open workspace</h3>
              <div class="action-row">
                <button type="button" class="ghost-button" data-view="run">Run view</button>
                <button type="button" class="ghost-button" data-view="snapshots">Snapshots view</button>
              </div>
            </div>
          </div>
        </section>
      </div>

      <div class="two-column">
        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Recent snapshots</h2>
              <p class="card-subtitle">The latest five captures stay close to the main operator flow.</p>
            </div>
            <button type="button" class="nav-inline-button" data-view="snapshots">Open all</button>
          </div>
          ${buildRecentSnapshotCards(5)}
        </section>

        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Activity</h2>
              <p class="card-subtitle">Short feedback from the backend and operator actions.</p>
            </div>
          </div>
          <div id="dashboard-activity" class="activity-list"></div>
        </section>
      </div>
    </div>
  `;
  renderActivityPanels();
}

function renderRun() {
  const model = activeModel();
  const run = runState();
  const root = document.getElementById("run-content");
  root.innerHTML = `
    <div class="run-layout">
      <div class="stack-layout">
        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Game selection</h2>
              <p class="card-subtitle">Compact list, active item highlighted.</p>
            </div>
          </div>
          ${buildChoiceButtons(appState.state ? appState.state.games : [], "games")}
        </section>

        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Model selection</h2>
              <p class="card-subtitle">Keep summaries compact and expose only the core model stats.</p>
            </div>
            <button type="button" class="ghost-button" data-action="reset-model">Reset active</button>
          </div>
          ${buildChoiceButtons(appState.state ? appState.state.models : [], "models")}
        </section>

        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Mode selection</h2>
              <p class="card-subtitle">Switch training mode without leaving the control surface.</p>
            </div>
          </div>
          ${buildChoiceButtons(appState.state ? appState.state.modes : [], "modes")}
        </section>
      </div>

      <div class="stack-layout">
        <section class="card accent-card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Run controls</h2>
              <p class="card-subtitle">Start or stop continuous ticking, or run deterministic step batches.</p>
            </div>
          </div>
          <div class="action-stack">
            <div class="action-row">
              <button type="button" class="action-button primary" data-action="run-start" ${run.running ? "disabled" : ""}>Start</button>
              <button type="button" class="action-button stop" data-action="run-stop" ${run.running ? "" : "disabled"}>Stop</button>
            </div>
            <div class="action-row">
              <button type="button" class="action-button secondary" data-run-steps="1">Step 1</button>
              <button type="button" class="action-button secondary" data-run-steps="10">Step 10</button>
              <button type="button" class="action-button secondary" data-run-steps="100">Step 100</button>
              <button type="button" class="action-button secondary" data-run-steps="1000">Step 1000</button>
            </div>
          </div>
        </section>

        <section class="card">
          <div class="card-header compact">
            <div>
              <h2 class="card-title">Current state</h2>
              <p class="card-subtitle">Run metrics and active model stats stay next to the controls.</p>
            </div>
          </div>
          ${buildMetricsGrid()}
          <div class="summary-grid" style="margin-top: 0.9rem;">
            ${summaryCard("Model params", model ? String(model.n_params) : "-", "Tracked by the active AI host.")}
            ${summaryCard("Hidden neurons", model ? String(model.n_neurons) : "-", "Current architecture footprint.")}
            ${summaryCard("Model steps", model ? String(model.steps) : "-", "Persistent steps on the selected model.")}
            ${summaryCard("Output", model ? `${formatNumber(model.logit)} / ${formatNumber(model.prob)}` : "-", "Latest model logit and probability.")}
          </div>
        </section>

        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Quick actions</h2>
              <p class="card-subtitle">The operator can save or analyze the current state immediately after any run step.</p>
            </div>
          </div>
          <div class="action-stack">
            <div class="action-group">
              <h3 class="action-group-title">Snapshot</h3>
              <div class="action-row">
                <button type="button" class="action-button subtle" data-action="save-snapshot">Save snapshot</button>
              </div>
            </div>
            <div class="action-group">
              <h3 class="action-group-title">Analyze current</h3>
              <div class="action-row">
                <button type="button" class="chip-button" data-analyze-current="chip">Chip</button>
                <button type="button" class="chip-button" data-analyze-current="forensic">Forensic</button>
                <button type="button" class="chip-button" data-analyze-current="prune">Prune</button>
                <button type="button" class="chip-button" data-action="graph-current">Graph</button>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  `;
}

function renderLab() {
  const snapshot = selectedSnapshot();
  const report = appState.report;
  const context = appState.reportContext;
  const root = document.getElementById("lab-content");
  root.innerHTML = `
    <div class="report-shell">
      <section class="card accent-card">
        <div class="card-header">
          <div>
            <h2 class="card-title">Lab toolbar</h2>
            <p class="card-subtitle">Choose an engine, then open the current state or the selected snapshot.</p>
          </div>
        </div>
        <div class="action-stack">
          <div class="action-group">
            <h3 class="action-group-title">Engine selector</h3>
            ${buildEngineButtons()}
          </div>
          <div class="action-group">
            <h3 class="action-group-title">Actions</h3>
            <div class="toolbar-row">
              <button type="button" class="action-button primary" data-action="report-current">Open current</button>
              <button type="button" class="action-button secondary" data-action="report-snapshot" ${snapshot ? "" : "disabled"}>Open selected snapshot</button>
              <button type="button" class="action-button subtle" data-action="save-snapshot">Save snapshot</button>
            </div>
          </div>
        </div>
      </section>

      <div class="two-column">
        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Report</h2>
              <p class="card-subtitle">Text output from the selected report engine.</p>
            </div>
          </div>
          <pre class="report-output">${escapeHtml(report ? report.body : "No report loaded. Open current or selected snapshot.")}</pre>
        </section>

        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">Report header</h2>
              <p class="card-subtitle">Current engine and snapshot context.</p>
            </div>
          </div>
          <div class="report-meta">
            <div class="detail-row">
              <span class="graph-label">Engine</span>
              <span class="detail-value">${escapeHtml(context ? context.engine : appState.selectedEngine)}</span>
            </div>
            <div class="detail-row">
              <span class="graph-label">Source</span>
              <span class="detail-value">${escapeHtml(context ? context.source : "none")}</span>
            </div>
            <div class="detail-row">
              <span class="graph-label">Snapshot</span>
              <span class="detail-value">${escapeHtml(context ? context.snapshotId : (snapshot ? snapshot.id : "-"))}</span>
            </div>
            <div class="detail-row">
              <span class="graph-label">Model / Game</span>
              <span class="detail-value">${escapeHtml(context ? `${context.model || "-"} / ${context.game || "-"}` : "-")}</span>
            </div>
            <div class="detail-row">
              <span class="graph-label">Mode</span>
              <span class="detail-value">${escapeHtml(context ? context.mode || "-" : "-")}</span>
            </div>
          </div>
        </section>
      </div>
    </div>
  `;
}

function appendGraphField(root, label, value) {
  if (value === undefined || value === null || value === "") {
    return;
  }
  const row = document.createElement("div");
  row.className = "detail-row";

  const term = document.createElement("span");
  term.className = "graph-label";
  term.textContent = label;

  const detail = document.createElement("span");
  detail.className = "detail-value";
  detail.textContent = String(value);

  row.append(term, detail);
  root.appendChild(row);
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
  renderGraphView();
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
  container.className = "graph-svg";

  if (graph.status !== "ok") {
    const empty = document.createElement("div");
    empty.className = "graph-empty";
    const message = document.createElement("p");
    message.className = "graph-message";
    message.textContent = graph.message || "Graph unsupported for this snapshot.";
    empty.appendChild(message);
    container.appendChild(empty);
    return container;
  }

  const currentCase = selectedGraphCase(graph, selectedCaseId);
  const hiddenCount = Math.max(graph.hidden.length, 1);
  const width = 760;
  const height = Math.max(340, 150 + hiddenCount * 74);
  const top = 78;
  const bottom = 76;
  const hiddenStep = hiddenCount === 1 ? 0 : (height - top - bottom) / (hiddenCount - 1);
  const centerY = height / 2;

  const positions = {
    x0: { x: 120, y: centerY - 58 },
    x1: { x: 120, y: centerY + 58 },
    out: { x: 640, y: centerY },
  };

  graph.hidden.forEach((node, index) => {
    positions[node.id] = {
      x: 380,
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
    ? `Case ${currentCase.id}: x0=${currentCase.x0}, x1=${currentCase.x1}, logit=${formatNumber(currentCase.logit)}`
    : "All cases: topology and weights";
  svg.appendChild(title);

  const layerTitles = [
    { label: "inputs", x: positions.x0.x - 28 },
    { label: "hidden", x: positions.h0 ? positions.h0.x - 28 : 352 },
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
    const edgeGroup = createSvgElement("g");
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
      setGraphSelection({ nodeId: "", edgeId });
    });

    const midX = (from.x + to.x) / 2;
    const midY = (from.y + to.y) / 2;
    const weightLabel = createSvgElement("text", {
      x: midX + (edge.from === "x1" ? 10 : -6),
      y: midY + (edge.to === "out" ? -10 : edge.from === "x1" ? 14 : -10),
      class: `graph-weight ${edge.weight >= 0 ? "positive" : "negative"} ${isSelected ? "selected" : ""}`,
    });
    weightLabel.textContent = formatNumber(edge.weight);
    weightLabel.addEventListener("click", (event) => {
      event.stopPropagation();
      setGraphSelection({ nodeId: "", edgeId });
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

    group.appendChild(createSvgElement("circle", {
      cx: position.x,
      cy: position.y,
      r: node.kind === "hidden" ? 24 : 22,
    }));

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
        ? `${node.role || "unknown"} · a=${formatNumber(hiddenState.activation)}`
        : (node.role || "unknown");
    } else if (node.kind === "output") {
      sideText.textContent = currentCase
        ? `y=${currentCase.network} · logit=${formatNumber(currentCase.logit)}`
        : `bias=${formatNumber(node.bias)}`;
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
        detail.textContent = `z=${formatNumber(hiddenState.z)}`;
        group.appendChild(detail);
      }
    }

    group.addEventListener("click", (event) => {
      event.stopPropagation();
      setGraphSelection({ nodeId: node.id, edgeId: "" });
    });
    svg.appendChild(group);
  }

  svg.addEventListener("click", () => {
    setGraphSelection({ edgeId: "" });
  });

  container.appendChild(svg);
  return container;
}

function buildGraphSummaryCards(graph) {
  const wrap = document.createElement("div");
  wrap.className = "graph-summary-grid";

  const cards = [
    { label: "Target", value: graph.target_role || "unknown", copy: "Target function guessed from the chip analysis." },
    { label: "Network", value: graph.network_role || "unknown", copy: "Observed function of the active network." },
    {
      label: "Match",
      value: typeof graph.match === "boolean" ? (graph.match ? "yes" : "no") : "n/a",
      copy: "Whether the network matches the target truth table.",
    },
    {
      label: "CMOS",
      value: typeof graph.cmos_transistors === "number" ? `${graph.cmos_transistors}T` : "n/a",
      copy: "Functional transistor cost from the chip engine.",
    },
  ];

  cards.forEach((item) => {
    const card = document.createElement("div");
    card.className = "summary-card";

    const label = document.createElement("div");
    label.className = "summary-label";
    label.textContent = item.label;

    const value = document.createElement("div");
    value.className = "summary-value";
    value.textContent = item.value;

    const copy = document.createElement("p");
    copy.className = "summary-copy";
    copy.textContent = item.copy;

    card.append(label, value, copy);
    wrap.appendChild(card);
  });

  return wrap;
}

function buildGraphInspector(payload) {
  const panel = document.createElement("section");
  panel.className = "card graph-sidebar";

  const header = document.createElement("div");
  header.className = "card-header compact";
  header.innerHTML = `
    <div>
      <h2 class="card-title">Details</h2>
      <p class="card-subtitle">Selected node, edge, case, and snapshot context.</p>
    </div>
  `;
  panel.appendChild(header);

  const details = document.createElement("div");
  details.className = "detail-list";
  const snapshot = payload.snapshot || {};
  const graph = payload.graph;
  const currentCase = selectedGraphCase(graph, appState.graphSelectedCaseId);

  appendGraphField(details, "Source", snapshot.id === "current" ? "current" : "snapshot");
  appendGraphField(details, "Snapshot", snapshot.id || "-");
  appendGraphField(details, "Model", snapshot.model || "-");
  appendGraphField(details, "Game", snapshot.game || "-");
  appendGraphField(details, "Mode", snapshot.mode || "-");
  appendGraphField(details, "Accuracy", snapshot.accuracy ? `${snapshot.accuracy}%` : "-");
  appendGraphField(details, "Case", currentCase ? currentCase.id : "all");

  if (graph.status !== "ok") {
    appendGraphField(details, "Status", "unsupported");
    appendGraphField(details, "Message", graph.message || "Graph unsupported for this snapshot.");
    panel.appendChild(details);
    return panel;
  }

  const selectedEdge = findGraphEdge(graph, appState.graphSelectedEdgeId);
  if (selectedEdge) {
    appendGraphField(details, "Selected edge", `${selectedEdge.from} -> ${selectedEdge.to}`);
    appendGraphField(details, "Weight", formatNumber(selectedEdge.weight));
    appendGraphField(details, "Sign", selectedEdge.weight >= 0 ? "positive" : "negative");
    panel.appendChild(details);
    return panel;
  }

  const node = graphNodeById(graph, appState.graphSelectedNodeId);
  if (!node) {
    appendGraphField(details, "Selection", "none");
    appendGraphField(details, "Hint", "Click a node, edge, or truth-table row.");
    panel.appendChild(details);
    return panel;
  }

  appendGraphField(details, "Node", node.label || node.id);
  appendGraphField(details, "Type", node.kind);

  if (node.kind === "input") {
    appendGraphField(details, "Value", currentCase ? currentCase[node.id] : "all cases");
  }

  if (node.kind === "hidden") {
    appendGraphField(details, "Role", node.role || "unknown");
    appendGraphField(details, "Bias", formatNumber(node.bias));
    appendGraphField(details, "Output weight", formatNumber(node.output_weight));
    appendGraphField(details, "Dead", node.dead ? "yes" : "no");
    (graph.edges || []).filter((edge) => edge.to === node.id).forEach((edge) => {
      appendGraphField(details, `${edge.from} -> ${node.id}`, formatNumber(edge.weight));
    });
    const hiddenState = currentCase && currentCase.hidden ? currentCase.hidden[node.id] : null;
    if (hiddenState) {
      appendGraphField(details, "z", formatNumber(hiddenState.z));
      appendGraphField(details, "activation", formatNumber(hiddenState.activation));
      appendGraphField(details, "Active", hiddenState.active ? "yes" : "no");
    }
  }

  if (node.kind === "output") {
    appendGraphField(details, "Bias", formatNumber(node.bias));
    (graph.edges || []).filter((edge) => edge.to === node.id).forEach((edge) => {
      appendGraphField(details, `${edge.from} -> out`, formatNumber(edge.weight));
    });
    if (currentCase) {
      appendGraphField(details, "Logit", formatNumber(currentCase.logit));
      appendGraphField(details, "Network", currentCase.network);
      appendGraphField(details, "Target", currentCase.target ?? "?");
    }
  }

  panel.appendChild(details);
  return panel;
}

function buildTruthTable(graph) {
  const panel = document.createElement("section");
  panel.className = "card table-card";

  const header = document.createElement("div");
  header.className = "card-header compact";
  header.innerHTML = `
    <div>
      <h2 class="card-title">Truth table</h2>
      <p class="card-subtitle">Compact case table with network result, target, and logit.</p>
    </div>
  `;
  panel.appendChild(header);

  if (graph.status !== "ok") {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `<p class="empty-copy">${escapeHtml(graph.message || "Graph unsupported for this snapshot.")}</p>`;
    panel.appendChild(empty);
    return panel;
  }

  const table = document.createElement("table");
  table.className = "graph-table";
  table.innerHTML = `
    <thead>
      <tr>
        <th>case</th>
        <th>x0</th>
        <th>x1</th>
        <th>target</th>
        <th>network</th>
        <th>logit</th>
        <th>ok</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;

  const tbody = table.querySelector("tbody");
  for (const row of graph.cases || []) {
    const tr = document.createElement("tr");
    if (row.id === appState.graphSelectedCaseId) {
      tr.classList.add("selected");
    }
    tr.addEventListener("click", () => {
      setGraphSelection({ caseId: row.id, edgeId: "" });
    });

    const values = [
      row.id,
      row.x0,
      row.x1,
      row.target ?? "?",
      row.network,
      formatNumber(row.logit),
      row.target === null || row.target === undefined ? "-" : (row.target === row.network ? "yes" : "no"),
    ];
    values.forEach((value) => {
      const td = document.createElement("td");
      td.textContent = String(value);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }

  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  wrap.appendChild(table);
  panel.appendChild(wrap);
  return panel;
}

function buildGraphToolbar(graph) {
  const snapshot = selectedSnapshot();
  const toolbarCard = document.createElement("section");
  toolbarCard.className = "card accent-card";

  const header = document.createElement("div");
  header.className = "card-header";
  header.innerHTML = `
    <div>
      <h2 class="card-title">Graph toolbar</h2>
      <p class="card-subtitle">Open the current graph, reopen the selected snapshot, or focus a specific truth-table case.</p>
    </div>
  `;
  toolbarCard.appendChild(header);

  const stack = document.createElement("div");
  stack.className = "action-stack";

  const actions = document.createElement("div");
  actions.className = "action-group";
  actions.innerHTML = `
    <h3 class="action-group-title">Open graph</h3>
    <div class="toolbar-row">
      <button type="button" class="action-button primary" data-action="graph-current">Open current graph</button>
      <button type="button" class="action-button secondary" data-action="graph-snapshot" ${snapshot ? "" : "disabled"}>Open selected snapshot graph</button>
    </div>
  `;
  stack.appendChild(actions);

  const cases = document.createElement("div");
  cases.className = "action-group";
  const caseRow = document.createElement("div");
  caseRow.className = "case-toolbar";
  const caseIds = graph && graph.status === "ok"
    ? ["all", ...(graph.cases || []).map((item) => item.id)]
    : ["all"];

  const caseTitle = document.createElement("h3");
  caseTitle.className = "action-group-title";
  caseTitle.textContent = "Case focus";
  cases.appendChild(caseTitle);

  caseIds.forEach((caseId) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `engine-button ${caseId === appState.graphSelectedCaseId ? "active" : ""}`;
    button.textContent = caseId;
    button.addEventListener("click", () => {
      setGraphSelection({ caseId, edgeId: "" });
    });
    caseRow.appendChild(button);
  });
  cases.appendChild(caseRow);
  stack.appendChild(cases);

  toolbarCard.appendChild(stack);
  return toolbarCard;
}

function renderGraphView() {
  const root = document.getElementById("graph-content");
  root.innerHTML = "";

  const payload = graphPayload();
  const graph = payload ? payload.graph : null;

  root.appendChild(buildGraphToolbar(graph));

  if (!payload || !graph) {
    const empty = document.createElement("section");
    empty.className = "card";
    empty.innerHTML = `
      <div class="empty-state">
        <p class="empty-copy">No graph loaded. Use "Open current graph" or select a snapshot in the Snapshots view.</p>
      </div>
    `;
    root.appendChild(empty);
    return;
  }

  const shell = document.createElement("div");
  shell.className = "graph-shell";

  const summary = document.createElement("section");
  summary.className = "card";
  const summaryHeader = document.createElement("div");
  summaryHeader.className = "card-header compact";
  summaryHeader.innerHTML = `
    <div>
      <h2 class="card-title">Graph summary</h2>
      <p class="card-subtitle">Target role, network role, truth-table match, and CMOS estimate.</p>
    </div>
  `;
  summary.appendChild(summaryHeader);
  summary.appendChild(buildGraphSummaryCards(graph));
  shell.appendChild(summary);

  const layout = document.createElement("div");
  layout.className = "graph-layout";

  const main = document.createElement("section");
  main.className = "card graph-main";
  const mainHeader = document.createElement("div");
  mainHeader.className = "card-header compact";
  mainHeader.innerHTML = `
    <div>
      <h2 class="card-title">Graph canvas</h2>
      <p class="card-subtitle">SVG network view with clickable nodes and edges.</p>
    </div>
  `;
  main.appendChild(mainHeader);
  const svgCard = document.createElement("div");
  svgCard.className = "graph-svg-card";
  svgCard.appendChild(drawNetworkSvg(graph, appState.graphSelectedCaseId));
  main.appendChild(svgCard);

  layout.append(main, buildGraphInspector(payload));
  shell.appendChild(layout);
  shell.appendChild(buildTruthTable(graph));
  root.appendChild(shell);
}

function renderSnapshots() {
  const snapshot = selectedSnapshot();
  const root = document.getElementById("snapshots-content");

  const tableRows = appState.snapshots.map((item) => `
    <tr class="${item.id === appState.selectedSnapshotId ? "selected" : ""}" data-snapshot-select="${escapeHtml(item.id)}">
      <td>
        <div class="snapshot-title">${escapeHtml(item.title)}</div>
        <div class="snapshot-path">${escapeHtml(item.path)}</div>
      </td>
      <td>${escapeHtml(item.model)}</td>
      <td>${escapeHtml(item.game)}</td>
      <td>${escapeHtml(item.mode)}</td>
      <td>${escapeHtml(item.accuracy)}%</td>
      <td>${escapeHtml(formatTimestamp(item.mtime))}</td>
      <td>
        <div class="inline-actions">
          <button type="button" class="mini-button" data-snapshot-report="${escapeHtml(item.id)}">Open report</button>
          <button type="button" class="mini-button" data-snapshot-graph="${escapeHtml(item.id)}">Open graph</button>
        </div>
      </td>
    </tr>
  `).join("");

  root.innerHTML = `
    <div class="snapshots-layout">
      <section class="card">
        <div class="card-header">
          <div>
            <h2 class="card-title">Snapshots</h2>
            <p class="card-subtitle">Compact list with immediate report and graph actions on each row.</p>
          </div>
          <div class="toolbar-row">
            <button type="button" class="action-button subtle" data-action="save-snapshot">Save current snapshot</button>
            <button type="button" class="action-button secondary" data-action="refresh-snapshots">Refresh</button>
          </div>
        </div>
        ${appState.snapshots.length ? `
          <div class="table-wrap">
            <table class="snapshot-table">
              <thead>
                <tr>
                  <th>title</th>
                  <th>model</th>
                  <th>game</th>
                  <th>mode</th>
                  <th>accuracy</th>
                  <th>time</th>
                  <th>actions</th>
                </tr>
              </thead>
              <tbody>${tableRows}</tbody>
            </table>
          </div>
        ` : `
          <div class="empty-state">
            <p class="empty-copy">No snapshots saved yet.</p>
          </div>
        `}
      </section>

      <section class="card">
        <div class="card-header">
          <div>
            <h2 class="card-title">Selected snapshot</h2>
            <p class="card-subtitle">Preview metadata and reopen the same snapshot without searching elsewhere.</p>
          </div>
        </div>
        ${snapshot ? `
          <div class="preview-list">
            <div class="detail-row">
              <span class="graph-label">Title</span>
              <span class="detail-value">${escapeHtml(snapshot.title)}</span>
            </div>
            <div class="detail-row">
              <span class="graph-label">Model / Game</span>
              <span class="detail-value">${escapeHtml(`${snapshot.model} / ${snapshot.game}`)}</span>
            </div>
            <div class="detail-row">
              <span class="graph-label">Mode</span>
              <span class="detail-value">${escapeHtml(snapshot.mode)}</span>
            </div>
            <div class="detail-row">
              <span class="graph-label">Accuracy</span>
              <span class="detail-value">${escapeHtml(snapshot.accuracy)}%</span>
            </div>
            <div class="detail-row">
              <span class="graph-label">Saved</span>
              <span class="detail-value">${escapeHtml(formatTimestamp(snapshot.mtime))}</span>
            </div>
            <div class="detail-row">
              <span class="graph-label">Path</span>
              <span class="detail-value">${escapeHtml(snapshot.path)}</span>
            </div>
            <div class="inline-actions">
              <button type="button" class="action-button secondary" data-snapshot-report="${escapeHtml(snapshot.id)}">Open report</button>
              <button type="button" class="action-button secondary" data-snapshot-graph="${escapeHtml(snapshot.id)}">Open graph</button>
            </div>
          </div>
        ` : `
          <div class="empty-state">
            <p class="empty-copy">Select a snapshot row to pin it here.</p>
          </div>
        `}
      </section>
    </div>
  `;
}

function renderInfo() {
  const run = runState();
  const root = document.getElementById("info-content");
  root.innerHTML = `
    <div class="info-layout">
      <section class="card">
        <div class="card-header">
          <div>
            <h2 class="card-title">About game1</h2>
            <p class="card-subtitle">Web-only operator panel for local research runs.</p>
          </div>
        </div>
        <ul class="info-list">
          <li>Current architecture: web -> app -> modules.</li>
          <li>Operator flow: choose game/model/mode, run or step, then analyze current or save snapshot.</li>
          <li>Lab engines: Chip, Forensic, Prune.</li>
          <li>Supported graph inspector: MLP 2->N->1 snapshots.</li>
          <li>No external dependencies, no npm, no CDN, no build step.</li>
        </ul>
      </section>

      <section class="card">
        <div class="card-header">
          <div>
            <h2 class="card-title">Current context</h2>
            <p class="card-subtitle">Small live summary of the active operator session.</p>
          </div>
        </div>
        <div class="preview-list">
          <div class="detail-row">
            <span class="graph-label">Game</span>
            <span class="detail-value">${escapeHtml(run.active_game || "-")}</span>
          </div>
          <div class="detail-row">
            <span class="graph-label">Model</span>
            <span class="detail-value">${escapeHtml(run.active_model || "-")}</span>
          </div>
          <div class="detail-row">
            <span class="graph-label">Mode</span>
            <span class="detail-value">${escapeHtml(run.active_mode || "-")}</span>
          </div>
          <div class="detail-row">
            <span class="graph-label">Run</span>
            <span class="detail-value">${run.running ? "running" : "stopped"}</span>
          </div>
          <div class="detail-row">
            <span class="graph-label">Selected snapshot</span>
            <span class="detail-value">${escapeHtml(appState.selectedSnapshotId || "-")}</span>
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderActivityPanels() {
  const targets = ["dashboard-activity"];
  const entries = appState.logs.length ? appState.logs : [{
    timestamp: "--:--:--",
    level: "info",
    message: "No events yet.",
  }];

  targets.forEach((id) => {
    const root = document.getElementById(id);
    if (!root) {
      return;
    }
    root.innerHTML = entries.map((entry) => `
      <div class="log-entry ${escapeHtml(entry.level)}">
        <div class="log-meta">
          <span class="log-level">${escapeHtml(entry.level)}</span>
          <span class="log-time">${escapeHtml(entry.timestamp)}</span>
        </div>
        <p class="log-message">${escapeHtml(entry.message)}</p>
      </div>
    `).join("");
  });
}

function renderApp() {
  renderShell();
  renderDashboard();
  renderRun();
  renderLab();
  renderGraphView();
  renderSnapshots();
  renderInfo();
}

function applyState(state) {
  appState.state = state;
  syncTicker(state.run.running);
  renderApp();
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

  if (appState.selectedSnapshotId && !appState.snapshots.some((item) => item.id === appState.selectedSnapshotId)) {
    appState.selectedSnapshotId = "";
  }
  if (!appState.selectedSnapshotId && appState.snapshots.length) {
    appState.selectedSnapshotId = appState.snapshots[0].id;
  }
  renderDashboard();
  renderLab();
  renderSnapshots();
  renderInfo();
}

async function refreshEngines() {
  const data = await apiRequest("/api/lab/engines", {}, { quiet: true });
  appState.engines = data.engines;
  if (!appState.engines.some((item) => item.key === appState.selectedEngine) && appState.engines.length) {
    appState.selectedEngine = appState.engines[0].key;
  }
  renderLab();
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
    syncTicker(data.run.running);
    renderApp();
  } catch (error) {
    logLine(error.message, "error");
    syncTicker(false);
  } finally {
    appState.tickInFlight = false;
  }
}

async function performAction(action, options = {}) {
  try {
    const result = await action();
    if (options.refreshState !== false) {
      await refreshState();
    }
    if (options.refreshSnapshots) {
      await refreshSnapshots();
    }
    if (options.refreshEngines) {
      await refreshEngines();
    }
    return result;
  } catch (error) {
    logLine(error.message, "error");
    return null;
  }
}

async function selectSnapshot(snapshotId) {
  appState.selectedSnapshotId = snapshotId;
  renderDashboard();
  renderLab();
  renderSnapshots();
  renderInfo();
}

async function saveSnapshot() {
  try {
    const data = await apiRequest("/api/snapshots/save", { method: "POST" });
    if (data.snapshot && data.snapshot.id) {
      appState.selectedSnapshotId = data.snapshot.id;
    }
    await refreshSnapshots();
    await refreshState();
  } catch (error) {
    logLine(error.message, "error");
  }
}

async function resetModel() {
  const run = runState();
  if (!run.active_model) {
    logLine("No active model to reset.", "warn");
    return;
  }
  await performAction(() => apiRequest("/api/models/reset", {
    method: "POST",
    body: { key: run.active_model },
  }));
}

async function runSteps(steps) {
  await performAction(() => apiRequest("/api/run/steps", {
    method: "POST",
    body: { steps },
  }));
}

async function openCurrentReport(engineKey = appState.selectedEngine) {
  appState.selectedEngine = engineKey;
  try {
    const data = await apiRequest("/api/lab/report-current", {
      method: "POST",
      body: { engine: engineKey },
    });
    appState.report = data.report;
    appState.reportContext = {
      engine: engineKey,
      source: "current",
      snapshotId: data.snapshot ? data.snapshot.id : "current",
      model: data.snapshot ? data.snapshot.model : "",
      game: data.snapshot ? data.snapshot.game : "",
      mode: data.snapshot ? data.snapshot.mode : "",
    };
    setView("lab");
    renderLab();
    logLine(`Opened ${engineKey} report for current state.`, "ok");
  } catch (error) {
    logLine(error.message, "error");
  }
}

async function openSnapshotReport(snapshotId = appState.selectedSnapshotId) {
  if (!snapshotId) {
    logLine("Select a snapshot first.", "warn");
    return;
  }
  try {
    const params = new URLSearchParams({
      snapshot: snapshotId,
      engine: appState.selectedEngine,
    });
    const data = await apiRequest(`/api/lab/report?${params.toString()}`, {}, { quiet: true });
    const snapshot = appState.snapshots.find((item) => item.id === snapshotId) || null;
    appState.report = data.report;
    appState.reportContext = {
      engine: appState.selectedEngine,
      source: "snapshot",
      snapshotId,
      model: snapshot ? snapshot.model : "",
      game: snapshot ? snapshot.game : "",
      mode: snapshot ? snapshot.mode : "",
    };
    appState.selectedSnapshotId = snapshotId;
    setView("lab");
    renderLab();
    renderDashboard();
    renderSnapshots();
    renderInfo();
    logLine(`Opened ${appState.selectedEngine} report for snapshot ${snapshotId}.`, "ok");
  } catch (error) {
    logLine(error.message, "error");
  }
}

async function openCurrentGraph() {
  try {
    const data = await apiRequest("/api/graph-current", { method: "POST" });
    appState.graph = data;
    appState.graphSelectedCaseId = "all";
    appState.graphSelectedEdgeId = "";
    appState.graphSelectedNodeId = data.graph && data.graph.output && data.graph.output.id
      ? data.graph.output.id
      : "";
    setView("graph");
    renderGraphView();
    logLine("Opened graph inspector for current state.", "ok");
  } catch (error) {
    logLine(error.message, "error");
  }
}

async function openSnapshotGraph(snapshotId = appState.selectedSnapshotId) {
  if (!snapshotId) {
    logLine("Select a snapshot first.", "warn");
    return;
  }
  try {
    const params = new URLSearchParams({ snapshot: snapshotId });
    const data = await apiRequest(`/api/graph?${params.toString()}`, {}, { quiet: true });
    appState.graph = data;
    appState.graphSelectedCaseId = "all";
    appState.graphSelectedEdgeId = "";
    appState.graphSelectedNodeId = data.graph && data.graph.output && data.graph.output.id
      ? data.graph.output.id
      : "";
    appState.selectedSnapshotId = snapshotId;
    setView("graph");
    renderDashboard();
    renderGraphView();
    renderSnapshots();
    renderInfo();
    logLine(`Opened graph inspector for snapshot ${snapshotId}.`, "ok");
  } catch (error) {
    logLine(error.message, "error");
  }
}

async function handleAction(actionName) {
  if (actionName === "run-start") {
    await performAction(() => apiRequest("/api/run/start", { method: "POST" }));
    return;
  }
  if (actionName === "run-stop") {
    await performAction(() => apiRequest("/api/run/stop", { method: "POST" }));
    return;
  }
  if (actionName === "save-snapshot") {
    await saveSnapshot();
    return;
  }
  if (actionName === "reset-model") {
    await resetModel();
    return;
  }
  if (actionName === "report-current") {
    await openCurrentReport();
    return;
  }
  if (actionName === "report-snapshot") {
    await openSnapshotReport();
    return;
  }
  if (actionName === "graph-current") {
    await openCurrentGraph();
    return;
  }
  if (actionName === "graph-snapshot") {
    await openSnapshotGraph();
    return;
  }
  if (actionName === "refresh-snapshots") {
    try {
      await refreshSnapshots();
      logLine("Snapshots refreshed.", "ok");
    } catch (error) {
      logLine(error.message, "error");
    }
  }
}

function bindUi() {
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }

    const viewButton = target.closest("[data-view]");
    if (viewButton instanceof HTMLElement) {
      setView(viewButton.dataset.view);
      return;
    }

    const actionButton = target.closest("[data-action]");
    if (actionButton instanceof HTMLElement) {
      void handleAction(actionButton.dataset.action);
      return;
    }

    const analyzeButton = target.closest("[data-analyze-current]");
    if (analyzeButton instanceof HTMLElement) {
      void openCurrentReport(analyzeButton.dataset.analyzeCurrent);
      return;
    }

    const stepsButton = target.closest("[data-run-steps]");
    if (stepsButton instanceof HTMLElement) {
      const steps = Number(stepsButton.dataset.runSteps);
      void runSteps(steps);
      return;
    }

    const engineButton = target.closest("[data-select-engine]");
    if (engineButton instanceof HTMLElement) {
      appState.selectedEngine = engineButton.dataset.selectEngine || appState.selectedEngine;
      renderLab();
      return;
    }

    const gameButton = target.closest("[data-select-game]");
    if (gameButton instanceof HTMLElement) {
      const key = gameButton.dataset.selectGame;
      void performAction(() => apiRequest("/api/games/select", {
        method: "POST",
        body: { key },
      }));
      return;
    }

    const modelButton = target.closest("[data-select-model]");
    if (modelButton instanceof HTMLElement) {
      const key = modelButton.dataset.selectModel;
      void performAction(() => apiRequest("/api/models/select", {
        method: "POST",
        body: { key },
      }));
      return;
    }

    const modeButton = target.closest("[data-select-mode]");
    if (modeButton instanceof HTMLElement) {
      const key = modeButton.dataset.selectMode;
      void performAction(() => apiRequest("/api/modes/select", {
        method: "POST",
        body: { key },
      }));
      return;
    }

    const snapshotReportButton = target.closest("[data-snapshot-report]");
    if (snapshotReportButton instanceof HTMLElement) {
      const snapshotId = snapshotReportButton.dataset.snapshotReport;
      void openSnapshotReport(snapshotId);
      return;
    }

    const snapshotGraphButton = target.closest("[data-snapshot-graph]");
    if (snapshotGraphButton instanceof HTMLElement) {
      const snapshotId = snapshotGraphButton.dataset.snapshotGraph;
      void openSnapshotGraph(snapshotId);
      return;
    }

    const snapshotSelectButton = target.closest("[data-snapshot-select]");
    if (snapshotSelectButton instanceof HTMLElement) {
      const snapshotId = snapshotSelectButton.dataset.snapshotSelect;
      void selectSnapshot(snapshotId);
    }
  });
}

async function init() {
  bindUi();
  renderApp();
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
