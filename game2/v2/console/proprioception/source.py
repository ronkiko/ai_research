"""Filter private Engine TELEMETRY into public self-body Proprioception."""
from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

from game2.v2.console.config import ProprioceptionSourceManifest
from game2.v2.console.transport.publisher import LatestPublisher
from game2.v2.contracts.framing import recv_frame
from game2.v2.contracts.proprioception import ProprioceptionFrame


def _connect(endpoint, timeout: float = 5.0) -> socket.socket:
    deadline = time.monotonic() + timeout
    while True:
        try:
            sock = socket.create_connection(
                (endpoint.host, endpoint.port), timeout=1
            )
            sock.settimeout(0.25)
            return sock
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def frame_from_telemetry(
    payload: dict, *, session_id: str, actor_id: str
) -> ProprioceptionFrame | None:
    """Project one private multi-Actor snapshot onto the strict self-body ABI."""
    if not isinstance(payload, dict):
        raise ValueError("Engine telemetry must be an object")
    if payload.get("version") != 1 or payload.get("type") != "telemetry":
        raise ValueError("unexpected Engine telemetry message")
    if payload.get("session_id") != session_id:
        raise ValueError("Engine telemetry session does not match")
    tick = payload.get("world_tick")
    actors = payload.get("actors")
    if type(tick) is not int or tick < 0 or not isinstance(actors, list):
        raise ValueError("Engine telemetry shape is invalid")
    actor = next(
        (
            item for item in actors
            if isinstance(item, dict) and item.get("actor_id") == actor_id
        ),
        None,
    )
    if actor is None:
        return None
    return ProprioceptionFrame(
        tick,
        actor.get("vx"),
        actor.get("vy"),
        actor.get("grounded"),
        actor.get("input_right"),
        actor.get("input_jump"),
    )


def run(manifest_path: str | Path) -> int:
    manifest = ProprioceptionSourceManifest.from_file(manifest_path)
    publisher = LatestPublisher(
        manifest.proprioception.host, manifest.proprioception.port
    )
    telemetry = None
    try:
        publisher.start()
        telemetry = _connect(manifest.engine_telemetry)
        print(
            "READY " + json.dumps({
                "session_id": manifest.session_id,
                "proprioception": {
                    "host": publisher.host,
                    "port": publisher.port,
                },
            }, sort_keys=True),
            flush=True,
        )
        while True:
            try:
                payload = recv_frame(telemetry)
            except socket.timeout:
                continue
            frame = frame_from_telemetry(
                payload,
                session_id=manifest.session_id,
                actor_id=manifest.self_actor_id,
            )
            if frame is not None and publisher.subscriber_count():
                publisher.publish(frame.to_payload(manifest.session_id))
    except (EOFError, KeyboardInterrupt):
        return 0
    finally:
        if telemetry is not None:
            try:
                telemetry.close()
            except OSError:
                pass
        publisher.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    return run(args.manifest)


if __name__ == "__main__":
    raise SystemExit(main())
