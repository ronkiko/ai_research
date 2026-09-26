import {validateInputState} from "./protocol.mjs";

export class TokenBucket {
  constructor(ratePerSecond, nowMs = Date.now()) {
    if (!Number.isFinite(ratePerSecond) || ratePerSecond <= 0) {
      throw new Error("ratePerSecond must be positive");
    }
    this.capacity = ratePerSecond;
    this.tokens = ratePerSecond;
    this.ratePerMs = ratePerSecond / 1000;
    this.updatedAt = nowMs;
  }

  take(nowMs = Date.now()) {
    const elapsed = Math.max(0, nowMs - this.updatedAt);
    this.updatedAt = nowMs;
    this.tokens = Math.min(this.capacity, this.tokens + elapsed * this.ratePerMs);
    if (this.tokens < 1) return false;
    this.tokens -= 1;
    return true;
  }
}

export class HumanInputBuffer {
  constructor({
    maxUpstreamHz = 20,
    keepaliveMs = 500,
    eventBudgetPerSecond = 120,
    nowMs = Date.now(),
  } = {}) {
    if (!Number.isFinite(maxUpstreamHz) || maxUpstreamHz <= 0) {
      throw new Error("maxUpstreamHz must be positive");
    }
    this.minIntervalMs = 1000 / maxUpstreamHz;
    this.keepaliveMs = keepaliveMs;
    this.bucket = new TokenBucket(eventBudgetPerSecond, nowMs);
    this.reset(nowMs);
  }

  reset(nowMs = Date.now()) {
    this.desiredAxis = 0;
    this.lastSentAxis = 0;
    this.lastSentAt = null;
    this.lastClientSequence = -1;
    this.dirty = false;
    this.received = 0;
    this.coalesced = 0;
    this.rateLimited = 0;
    this.bucket.tokens = this.bucket.capacity;
    this.bucket.updatedAt = nowMs;
  }

  receive(payload, nowMs = Date.now()) {
    const value = validateInputState(payload);
    if (value.sequence <= this.lastClientSequence) {
      throw new Error("input_state sequence must increase");
    }
    this.lastClientSequence = value.sequence;
    this.received += 1;
    if (!this.bucket.take(nowMs)) {
      this.rateLimited += 1;
      throw new Error("input_state rate limit exceeded");
    }
    const changed = value.axis_x !== this.desiredAxis;
    if (changed) {
      this.desiredAxis = value.axis_x;
      this.dirty = true;
    } else {
      this.coalesced += 1;
    }
    return {changed, sequence: value.sequence, axis_x: value.axis_x};
  }

  due(nowMs = Date.now()) {
    if (this.lastSentAt == null) return this.dirty;
    const elapsed = Math.max(0, nowMs - this.lastSentAt);
    if (this.dirty && elapsed >= this.minIntervalMs) return true;
    return this.desiredAxis !== 0 && elapsed >= this.keepaliveMs;
  }

  consume(nowMs = Date.now(), {force = false} = {}) {
    if (!force && !this.due(nowMs)) return null;
    if (force && !this.dirty && this.desiredAxis === this.lastSentAxis && this.desiredAxis === 0) {
      return null;
    }
    const axis = this.desiredAxis;
    this.lastSentAxis = axis;
    this.lastSentAt = nowMs;
    this.dirty = false;
    return axis;
  }

  release(nowMs = Date.now()) {
    if (this.desiredAxis !== 0 || this.lastSentAxis !== 0) {
      this.desiredAxis = 0;
      this.dirty = true;
    }
    return this.consume(nowMs, {force: true});
  }

  metrics() {
    return {
      received: this.received,
      coalesced: this.coalesced,
      rate_limited: this.rateLimited,
      desired_axis: this.desiredAxis,
      last_sent_axis: this.lastSentAxis,
    };
  }
}
