import {FrameGate} from "./frame-gate.mjs";
import {HumanInputBuffer} from "./input-buffer.mjs";
import {projectHostState, terrainFor} from "./projector.mjs";

export class PlayerGatewayCore {
  constructor({
    stateClient,
    controlClient,
    catalog,
    frameHz = 25,
    inputHz = 20,
    inputKeepaliveMs = 500,
    inputEventsPerSecond = 120,
    clock = () => Date.now(),
  }) {
    this.stateClient = stateClient;
    this.controlClient = controlClient;
    this.catalog = catalog;
    this.frameHz = frameHz;
    this.inputHz = inputHz;
    this.clock = clock;
    this.input = new HumanInputBuffer({
      maxUpstreamHz: inputHz,
      keepaliveMs: inputKeepaliveMs,
      eventBudgetPerSecond: inputEventsPerSecond,
      nowMs: clock(),
    });
    this.frameGate = new FrameGate();
    this.owner = null;
    this.leaseId = null;
    this.latestFrame = null;
    this.latestTerrain = null;
    this.latestHostFreshness = null;
    this.lastError = null;
    this.frameTimer = null;
    this.inputTimer = null;
    this.frameBusy = false;
    this.controlQueue = Promise.resolve();
    this.metricsState = {
      frames_published: 0,
      frames_rejected: 0,
      state_errors: 0,
      last_state_latency_ms: null,
      last_frame_at_ms: null,
    };
    this.onFrame = () => {};
    this.onStatus = () => {};
    this.onInput = () => {};
  }

  start({onFrame, onStatus, onInput} = {}) {
    if (onFrame) this.onFrame = onFrame;
    if (onStatus) this.onStatus = onStatus;
    if (onInput) this.onInput = onInput;
    if (!this.frameTimer) {
      this.frameTimer = setInterval(() => {
        void this.pollFrame();
      }, Math.max(10, Math.floor(1000 / this.frameHz)));
    }
    if (!this.inputTimer) {
      this.inputTimer = setInterval(() => {
        void this.flushInput();
      }, Math.max(10, Math.floor(500 / this.inputHz)));
    }
    void this.pollFrame();
  }

  async stop() {
    if (this.frameTimer) clearInterval(this.frameTimer);
    if (this.inputTimer) clearInterval(this.inputTimer);
    this.frameTimer = null;
    this.inputTimer = null;
    if (this.owner) {
      try { await this.release(this.owner); } catch {}
    }
    this.stateClient.close();
    this.controlClient.close();
  }

  async pollFrame() {
    if (this.frameBusy) return null;
    this.frameBusy = true;
    try {
      const started = this.clock();
      const state = await this.stateClient.request("state");
      this.metricsState.last_state_latency_ms = Math.max(0, this.clock() - started);
      this.latestHostFreshness = state.freshness || null;
      if (state.freshness?.stale) {
        this.lastError = "authoritative Host state is stale";
        this.onStatus(this.status());
        return null;
      }
      const frame = projectHostState(state, this.catalog, {source: "player_gateway_v1"});
      const verdict = this.frameGate.accept(frame);
      if (!verdict.accepted) {
        this.metricsState.frames_rejected += 1;
        return null;
      }
      this.latestFrame = frame;
      this.latestTerrain = terrainFor(this.catalog, frame.zone_id);
      const recovered = this.lastError !== null;
      this.lastError = null;
      this.metricsState.frames_published += 1;
      this.metricsState.last_frame_at_ms = this.clock();
      this.onFrame(frame, verdict.reset);
      if (recovered) this.onStatus(this.status());
      return frame;
    } catch (error) {
      this.metricsState.state_errors += 1;
      this.lastError = error.message || String(error);
      this.onStatus(this.status());
      return null;
    } finally {
      this.frameBusy = false;
    }
  }

  #queueControl(fn) {
    const task = this.controlQueue.then(fn);
    this.controlQueue = task.catch(() => {});
    return task;
  }

  async acquire(ownerId) {
    if (typeof ownerId !== "string" || !ownerId) throw new Error("ownerId is required");
    if (this.owner && this.owner !== ownerId) {
      throw new Error("human control is already owned by another browser session");
    }
    if (this.owner === ownerId && this.leaseId) return this.controlStatus(ownerId);
    return this.#queueControl(async () => {
      if (this.owner && this.owner !== ownerId) {
        throw new Error("human control is already owned by another browser session");
      }
      const response = await this.controlClient.request("control_acquire", {transfer: true});
      const leaseId = response.lease?.lease_id;
      if (typeof leaseId !== "string" || !leaseId) throw new Error("Host did not issue a manual lease");
      this.owner = ownerId;
      this.leaseId = leaseId;
      this.input.reset(this.clock());
      this.onStatus(this.status());
      return this.controlStatus(ownerId);
    });
  }

  controlStatus(ownerId = null) {
    return {
      owned: this.owner !== null,
      owned_by_you: ownerId != null && ownerId === this.owner,
    };
  }

  offerInput(ownerId, payload) {
    if (ownerId !== this.owner || !this.leaseId) {
      throw new Error("browser session does not own human control");
    }
    const accepted = this.input.receive(payload, this.clock());
    void this.flushInput();
    return accepted;
  }

  async flushInput() {
    if (!this.owner || !this.leaseId) return null;
    const axis = this.input.consume(this.clock());
    if (axis == null) return null;
    const ownerAtQueue = this.owner;
    const leaseAtQueue = this.leaseId;
    return this.#queueControl(async () => {
      if (ownerAtQueue !== this.owner || leaseAtQueue !== this.leaseId) return null;
      const response = await this.controlClient.request("input", {
        move_x: axis,
        lease_id: leaseAtQueue,
      });
      this.onInput({
        owner_id: ownerAtQueue,
        axis_x: axis,
        host_sequence: response.sequence,
      });
      return response;
    }).catch((error) => {
      this.lastError = error.message || String(error);
      this.onStatus(this.status());
      return null;
    });
  }

  async release(ownerId) {
    if (ownerId !== this.owner || !this.leaseId) return false;
    const leaseId = this.leaseId;
    const axis = this.input.release(this.clock());
    return this.#queueControl(async () => {
      try {
        if (axis != null) {
          await this.controlClient.request("input", {
            move_x: axis,
            lease_id: leaseId,
          });
        }
      } finally {
        try {
          await this.controlClient.request("control_release", {lease_id: leaseId});
        } finally {
          if (this.owner === ownerId && this.leaseId === leaseId) {
            this.owner = null;
            this.leaseId = null;
            this.input.reset(this.clock());
            this.onStatus(this.status());
          }
        }
      }
      return true;
    });
  }

  status() {
    const now = this.clock();
    return {
      version: 1,
      component: "player_gateway",
      status: this.lastError ? "degraded" : "ready",
      control_owned: this.owner !== null,
      frame_id: this.latestFrame?.frame_id || null,
      world_epoch: this.latestFrame?.source_world_epoch || null,
      world_tick: this.latestFrame?.source_world_tick ?? null,
      zone_id: this.latestFrame?.zone_id || null,
      host_freshness: this.latestHostFreshness,
      input: this.input.metrics(),
      metrics: {
        ...this.metricsState,
        frame_age_ms: this.metricsState.last_frame_at_ms == null
          ? null
          : Math.max(0, now - this.metricsState.last_frame_at_ms),
      },
      error: this.lastError,
    };
  }
}
