const socket = io();
const canvas = document.getElementById("world");
const ctx = canvas.getContext("2d");
const connection = document.getElementById("connection");
const meta = document.getElementById("world-meta");
const control = document.getElementById("control");
const status = document.getElementById("status");

let assets = {};
let frame = null;
let controlOwned = false;
let sequence = 0;
let axis = 0;
const held = new Set();

fetch("/api/assets").then((response) => response.json()).then((value) => {
  assets = value.assets || {};
  draw();
});

function emitAck(name, payload) {
  return new Promise((resolve, reject) => {
    socket.timeout(1200).emit(name, payload, (error, value) => {
      if (error) reject(new Error("gateway timeout"));
      else if (!value?.ok) reject(new Error(value?.error || "gateway request failed"));
      else resolve(value);
    });
  });
}

async function acquire() {
  if (controlOwned) return;
  const value = await emitAck("control.acquire", {version: 1});
  controlOwned = value.control?.owned_by_you === true;
  control.textContent = controlOwned ? "acquired" : "not acquired";
}

async function sendAxis(next) {
  if (next === axis) return;
  axis = next;
  try {
    await acquire();
    sequence += 1;
    await emitAck("input_state", {version: 1, sequence, axis_x: axis});
  } catch (error) {
    status.textContent = error.message;
    status.className = "error";
    controlOwned = false;
    control.textContent = "not acquired";
  }
}

function desiredAxis() {
  if (held.has("ArrowLeft") && !held.has("ArrowRight")) return -1;
  if (held.has("ArrowRight") && !held.has("ArrowLeft")) return 1;
  return 0;
}

window.addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  if (event.repeat) return;
  held.add(event.key);
  void sendAxis(desiredAxis());
});
window.addEventListener("keyup", (event) => {
  if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  event.preventDefault();
  held.delete(event.key);
  void sendAxis(desiredAxis());
});

async function release() {
  held.clear();
  axis = 0;
  if (!controlOwned) return;
  controlOwned = false;
  try { await emitAck("control.release", {version: 1}); } catch {}
  control.textContent = "not acquired";
}
window.addEventListener("blur", () => { void release(); });
document.addEventListener("visibilitychange", () => {
  if (document.hidden) void release();
});
window.addEventListener("pagehide", () => { void release(); });

socket.on("connect", () => {
  connection.textContent = "connected";
  connection.className = "";
});
socket.on("disconnect", () => {
  connection.textContent = "disconnected";
  controlOwned = false;
  control.textContent = "not acquired";
});
socket.on("connect_error", (error) => {
  connection.textContent = "connection error";
  connection.className = "error";
  status.textContent = error.message;
});
socket.on("gateway.status", (value) => {
  if (value.control?.owned_by_you != null) {
    controlOwned = value.control.owned_by_you;
    control.textContent = controlOwned ? "acquired" : "not acquired";
  }
  const error = value.error || "";
  status.textContent = error || `frames: ${value.frame_id || "none"} · input received: ${value.input?.received ?? 0} · coalesced: ${value.input?.coalesced ?? 0}`;
  status.className = error ? "error" : "";
});
socket.on("frame.latest", (value) => {
  if (value?.version !== 1 || !value.frame) return;
  frame = value.frame;
  meta.textContent = `${frame.zone_id} · tick ${frame.source_world_tick} · rev ${frame.source_world_revision}`;
  draw();
});
socket.on("input.applied", (value) => {
  if (value?.host_sequence != null) {
    status.textContent = `Host input sequence ${value.host_sequence} · axis ${value.axis_x}`;
    status.className = "";
  }
});

function asset(id) {
  return assets[id] || {fill:"#809098",stroke:"#405058",glyph:"?"};
}

function worldToX(value) {
  if (!frame) return 0;
  return 30 + value * (canvas.width - 60);
}

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!frame) {
    ctx.fillStyle = "#aebdc4";
    ctx.font = "20px system-ui";
    ctx.fillText("Waiting for authoritative RenderFrame…", 30, 50);
    return;
  }
  const bg = asset("background." + ({
    hallway:"hallway_day",
    laboratory:"laboratory_day",
    "training/flat_run":"training_flat_run",
  })[frame.zone_id]);
  ctx.fillStyle = bg.fill || "#26343a";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const groundY = 255;
  ctx.strokeStyle = bg.ground || "#829592";
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.moveTo(30, groundY);
  ctx.lineTo(canvas.width - 30, groundY);
  ctx.stroke();

  for (const prop of frame.props || []) {
    const info = asset(prop.visual_asset);
    const x = worldToX((prop.x - frame.camera.world_min) / (frame.camera.world_max - frame.camera.world_min));
    ctx.fillStyle = info.fill || "#667";
    ctx.fillRect(x - 5, groundY - 28, 10, 28);
    ctx.fillStyle = "#e7ecef";
    ctx.font = "12px system-ui";
    ctx.fillText(info.label || prop.kind, x - 20, groundY + 28);
  }
  for (const entity of frame.entities || []) {
    const info = asset(entity.visual_asset);
    const x = worldToX(entity.screen_x);
    ctx.fillStyle = info.fill || "#809098";
    ctx.strokeStyle = info.stroke || "#405058";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(x, groundY - 34, 18, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.font = "bold 16px system-ui";
    ctx.textAlign = "center";
    ctx.fillText(info.glyph || "•", x, groundY - 29);
    ctx.font = "12px system-ui";
    ctx.fillText(info.label || entity.entity_id, x, groundY - 58);
  }
  ctx.textAlign = "start";
}
draw();
