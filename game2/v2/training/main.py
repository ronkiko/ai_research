"""Standalone loopback Trainer runtime for the first V2 training vertical."""
from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import time
from dataclasses import dataclass, field
from numbers import Real
from typing import Any

from game2.v2.contracts.training import (
    APPLY_RESULT,
    BEGIN_EPISODE,
    EVALUATE,
    EPISODE_FINISHED,
    EPISODE_STARTED,
    PREPARE,
    READY,
    SAVE,
    SAVED,
    TRAIN,
    UPDATE_RESULT,
    apply_result_message,
    begin_episode_message,
    prepare_message,
    recv_training_message,
    save_message,
    send_training_message,
)


def reward_for_result(result: str, progress: Real) -> float:
    if type(progress) is bool or not isinstance(progress, Real) \
            or not math.isfinite(float(progress)) or not 0.0 <= float(progress) <= 1.0:
        raise ValueError("progress must be finite and in [0.0, 1.0]")
    if result == "success":
        return 1.0
    if result in {"timeout", "dead"}:
        return -1.0
    raise ValueError("unknown terminal result")


@dataclass
class TrainerSummary:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    trainable_episodes: int = 0
    dirty_episodes: int = 0
    actual_update_count: int = 0
    losses: list[float] = field(default_factory=list)
    stopped_on_success: bool = False
    mastered: bool = False

    @property
    def success_rate_total(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "trainable_episodes": self.trainable_episodes,
            "dirty_episodes": self.dirty_episodes,
            "actual_update_count": self.actual_update_count,
            "success_rate_total": self.success_rate_total,
            "losses": list(self.losses),
            "stopped_on_success": self.stopped_on_success,
            "mastered": self.mastered,
        }


class Trainer:
    """One Trainer server connected to exactly one learned Player peer."""

    def __init__(self, *, listen_host: str = "127.0.0.1", listen_port: int = 9000,
                 mode: str = TRAIN, episodes: int = 1, seed: int = 100,
                 accept_timeout: float = 30.0, stop_on_success: bool = False):
        if type(listen_host) is not str or not listen_host:
            raise ValueError("listen_host must be non-empty")
        if type(listen_port) is not int or not 0 <= listen_port <= 65535:
            raise ValueError("listen_port must be in 0..65535")
        if mode not in {TRAIN, "evaluate"}:
            raise ValueError("mode must be train or evaluate")
        if type(episodes) is not int or episodes <= 0:
            raise ValueError("episodes must be a positive integer")
        if type(seed) is not int:
            raise ValueError("seed must be an integer")
        if accept_timeout <= 0:
            raise ValueError("accept_timeout must be positive")
        if type(stop_on_success) is not bool:
            raise ValueError("stop_on_success must be boolean")
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.mode = mode
        self.episodes = episodes
        self.seed = seed
        self.accept_timeout = accept_timeout
        self.stop_on_success = stop_on_success
        self.bound_address: tuple[str, int] | None = None

    @staticmethod
    def _expect(sock: socket.socket, expected: str) -> dict[str, Any]:
        message = recv_training_message(sock)
        if message["type"] != expected:
            raise ValueError(f"expected {expected}, received {message['type']}")
        return message

    def _receive_episode(self, peer: socket.socket, episode_id: int, mode: str,
                         seed: int) -> dict[str, Any]:
        send_training_message(peer, prepare_message(episode_id, mode, seed))
        send_training_message(peer, begin_episode_message(episode_id))
        first_result = recv_training_message(peer)
        if first_result["type"] == EPISODE_STARTED:
            if first_result["episode_id"] != episode_id:
                raise ValueError("EPISODE_STARTED identity mismatch")
            finished = self._expect(peer, EPISODE_FINISHED)
            if (finished["episode_id"] != episode_id or
                    finished["start_world_tick"] != first_result["start_world_tick"]):
                raise ValueError("EPISODE_FINISHED identity or start tick mismatch")
        elif first_result["type"] == EPISODE_FINISHED:
            # A lifecycle or public-peripheral failure can make an attempt
            # dirty before a valid SELF frame exists, so no start event is
            # emitted for that attempt.
            finished = first_result
            if finished["episode_id"] != episode_id:
                raise ValueError("EPISODE_FINISHED identity mismatch")
        else:
            raise ValueError(f"expected {EPISODE_STARTED}, received {first_result['type']}")
        return finished

    def _run_peer(self, peer: socket.socket) -> TrainerSummary:
        peer.settimeout(self.accept_timeout)
        self._expect(peer, READY)
        peer.settimeout(None)
        summary = TrainerSummary()
        next_episode_id = 1
        for train_index in range(self.episodes):
            episode_id = next_episode_id
            next_episode_id += 1
            finished = self._receive_episode(
                peer, episode_id, self.mode, self.seed + train_index)

            if self.mode == EVALUATE:
                print("EVALUATION " + json.dumps({
                    "episode_id": episode_id,
                    "result": finished["result"],
                    "trainable": finished["trainable"],
                }, separators=(",", ":"), sort_keys=True), flush=True)
                continue

            summary.attempts += 1
            if finished["result"] == "success":
                summary.successes += 1
            else:
                summary.failures += 1
            if finished["trainable"]:
                summary.trainable_episodes += 1
            else:
                summary.dirty_episodes += 1

            reward = reward_for_result(finished["result"], finished["progress"]) \
                if finished["trainable"] else 0.0
            updated = False
            update = None
            if finished["trainable"]:
                update_started = time.monotonic()
                print("LEARNING " + json.dumps({
                    "episode_id": episode_id,
                    "status": "start",
                    "accepted_actions": finished["accepted_actions"],
                }, separators=(",", ":"), sort_keys=True), flush=True)
                send_training_message(peer, apply_result_message(episode_id, reward))
                update = self._expect(peer, UPDATE_RESULT)
                update_seconds = time.monotonic() - update_started
                print("LEARNING " + json.dumps({
                    "episode_id": episode_id,
                    "status": "done",
                    "seconds": round(update_seconds, 3),
                    "updated": update["updated"],
                }, separators=(",", ":"), sort_keys=True), flush=True)
                if update["episode_id"] != episode_id:
                    raise ValueError("UPDATE_RESULT identity mismatch")
                updated = update["updated"]
                if update["updated"]:
                    summary.actual_update_count += 1
                    summary.losses.append(update["loss"])
            loss = update["loss"] if updated and update is not None else None

            print("PROGRESS " + json.dumps({
                "episode_id": episode_id,
                "result": finished["result"],
                "trainable": finished["trainable"],
                "updated": updated,
                "progress": finished["progress"],
                "reward": reward,
                "attempts": summary.attempts,
                "successes": summary.successes,
                "accepted_actions": finished["accepted_actions"],
                "rejected_actions": finished["rejected_actions"],
                "loss": loss,
            }, separators=(",", ":"), sort_keys=True), flush=True)

            if (self.stop_on_success and finished["result"] == "success"
                    and finished["trainable"] and updated):
                evaluation_id = next_episode_id
                next_episode_id += 1
                evaluation = self._receive_episode(
                    peer, evaluation_id, EVALUATE, self.seed + evaluation_id)
                print("EVALUATION " + json.dumps({
                    "episode_id": evaluation_id,
                    "result": evaluation["result"],
                    "trainable": evaluation["trainable"],
                }, separators=(",", ":"), sort_keys=True), flush=True)
                if evaluation["result"] == "success" and evaluation["trainable"]:
                    summary.mastered = True
                    summary.stopped_on_success = True
                    break

        if self.mode == TRAIN:
            send_training_message(peer, save_message())
            self._expect(peer, SAVED)
        return summary

    def run(self) -> TrainerSummary:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.listen_host, self.listen_port))
            listener.listen(1)
            listener.settimeout(self.accept_timeout)
            self.bound_address = listener.getsockname()[:2]
            assert self.bound_address is not None
            print("READY " + json.dumps({
                "host": self.bound_address[0], "port": self.bound_address[1],
            }, separators=(",", ":"), sort_keys=True), flush=True)
            peer, _address = listener.accept()
            with peer:
                return self._run_peer(peer)
        finally:
            listener.close()


TrainerRuntime = Trainer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Game2 V2 Trainer")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=9000)
    parser.add_argument("--mode", choices=(TRAIN, "evaluate"), default=TRAIN)
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--seed", type=int, default=100)
    parser.add_argument("--stop-on-success", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = Trainer(
            listen_host=args.listen_host, listen_port=args.listen_port,
            mode=args.mode, episodes=args.episodes, seed=args.seed,
            stop_on_success=args.stop_on_success,
        ).run()
    except (EOFError, OSError, TimeoutError, ValueError, ConnectionError) as exc:
        print(f"ERROR Trainer failed: {exc}", file=sys.stderr, flush=True)
        return 1
    print("SUMMARY " + json.dumps(summary.to_dict(), separators=(",", ":"),
                                  sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
