export class FrameGate {
  constructor() {
    this.activeEpoch = null;
    this.lastRevision = -1;
    this.lastTick = -1;
    this.lastFrameId = null;
  }

  accept(frame) {
    const epoch = frame.source_world_epoch;
    const revision = frame.source_world_revision;
    const tick = frame.source_world_tick;
    if (frame.frame_id === this.lastFrameId) return {accepted: false, reset: false};
    let reset = false;
    if (this.activeEpoch == null) {
      this.activeEpoch = epoch;
    } else if (epoch !== this.activeEpoch) {
      if (frame.freshness?.previous_epoch !== this.activeEpoch) {
        return {accepted: false, reset: false};
      }
      this.activeEpoch = epoch;
      this.lastRevision = -1;
      this.lastTick = -1;
      this.lastFrameId = null;
      reset = true;
    }
    if (
      revision < this.lastRevision ||
      (revision === this.lastRevision && tick <= this.lastTick)
    ) {
      return {accepted: false, reset: false};
    }
    this.lastRevision = revision;
    this.lastTick = tick;
    this.lastFrameId = frame.frame_id;
    return {accepted: true, reset};
  }
}
