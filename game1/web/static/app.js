const appState = {
  state: null,
  snapshots: [],
  engines: [],
  selectedSnapshotId: "",
  selectedEngine: "chip",
  tickerId: null,
  tickInFlight: false,
  logs: [],
};

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
}

async function init() {
  bindUi();
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
