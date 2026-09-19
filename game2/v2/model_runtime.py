"""Standalone OS process that owns learned inference, updates, and weights."""
from __future__ import annotations

import argparse
import json
import select
import socket
import sys
import time
from pathlib import Path

from game2.v2.contracts.framing import MAX_FRAME_SIZE, ProtocolError, decode_frame
from game2.v2.contracts.model import (
    ACTUATED,
    DECISION,
    EPISODE_END,
    OBSERVE,
    PREPARE,
    READY,
    SAVE,
    SAVED,
    UPDATE_RESULT,
    decode_model_message,
    decision_message,
    observation_from_message,
    ready_message,
    saved_message,
    send_model_message,
    update_result_message,
)
from game2.v2.player.learned.checkpoint import (
    load_critic,
    load_motor_controller,
    load_optimizer,
    load_planner,
)
from game2.v2.player.learned.critic import CNNCritic
from game2.v2.player.learned.motor import MotorController582
from game2.v2.player.learned.motion import self_center
from game2.v2.player.learned.planner import CNNPlanner
from game2.v2.player.learned.runtime import LearnedPlayer


def _checkpoint_paths(directory: str | Path) -> tuple[Path, Path, Path, Path]:
    root = Path(directory)
    return (
        root / "planner.pt",
        root / "motor.pt",
        root / "critic.pt",
        root / "optimizer.pt",
    )


def build_model(*, fresh: bool, planner_seed: int = 1, motor_seed: int = 2,
                critic_seed: int = 3,
                planner_checkpoint: str | Path | None = None,
                motor_checkpoint: str | Path | None = None,
                critic_checkpoint: str | Path | None = None,
                optimizer_checkpoint: str | Path | None = None) -> LearnedPlayer:
    checkpoints = (
        planner_checkpoint is not None,
        motor_checkpoint is not None,
        critic_checkpoint is not None,
        optimizer_checkpoint is not None,
    )
    if fresh:
        if any(checkpoints):
            raise ValueError("Fresh Model runtime cannot use checkpoints")
        planner = CNNPlanner.fresh(planner_seed)
        motor = MotorController582.fresh(motor_seed)
        critic = CNNCritic.fresh(critic_seed)
    else:
        if checkpoints != (True, True, True, True):
            raise ValueError(
                "Model resume requires planner, motor, critic, and optimizer checkpoints"
            )
        assert planner_checkpoint is not None
        assert motor_checkpoint is not None
        assert critic_checkpoint is not None
        assert optimizer_checkpoint is not None
        planner = load_planner(planner_checkpoint)
        motor = load_motor_controller(motor_checkpoint)
        critic = load_critic(critic_checkpoint)
    player = LearnedPlayer(planner, motor, critic)
    if not fresh:
        assert optimizer_checkpoint is not None
        assert player.optimizer is not None
        load_optimizer(player.optimizer, optimizer_checkpoint)
    return player


class _FrameReader:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def read_available(self, sock: socket.socket) -> list[tuple[dict, bytes | None]]:
        messages = []
        while True:
            while True:
                if len(self.buffer) < 4:
                    break
                size = int.from_bytes(self.buffer[:4], "big")
                if size <= 0 or size > MAX_FRAME_SIZE:
                    raise ProtocolError("model frame size is invalid")
                if len(self.buffer) < size + 4:
                    break
                payload = bytes(self.buffer[4:size + 4])
                message = decode_model_message(decode_frame(payload))
                matrix_length = (
                    message.get("coarse_physics_length", 0)
                    + message.get("physics_length", 0)
                    + message.get("metadata_length", 0)
                    if message["type"] == OBSERVE else 0
                )
                total = size + 4 + matrix_length
                if len(self.buffer) < total:
                    break
                matrices = (
                    bytes(self.buffer[size + 4:total])
                    if message["type"] == OBSERVE else None
                )
                del self.buffer[:total]
                messages.append((message, matrices))
            if self.closed:
                if messages:
                    return messages
                raise EOFError("Player closed Model runtime IPC")
            try:
                chunk = sock.recv(65536)
            except BlockingIOError:
                break
            if not chunk:
                self.closed = True
                continue
            self.buffer.extend(chunk)
        return messages


class ModelRuntime:
    """One sequential model worker with an application-level latest mailbox."""

    def __init__(self, player: LearnedPlayer, *, listen_host: str = "127.0.0.1",
                 listen_port: int = 0, inference_delay: float = 0.0,
                 trajectory_log: str | Path | None = None):
        if not isinstance(listen_host, str) or not listen_host:
            raise ValueError("listen_host must be non-empty")
        if type(listen_port) is not int or not 0 <= listen_port <= 65535:
            raise ValueError("listen_port must be in 0..65535")
        if inference_delay < 0:
            raise ValueError("inference_delay must be non-negative")
        self.player = player
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.inference_delay = float(inference_delay)
        self.trajectory_log = (
            Path(trajectory_log) if trajectory_log is not None else None
        )
        self.bound_address: tuple[str, int] | None = None
        self._decision_id = 0
        self._samples: dict[int, object] = {}
        self._episode_id: int | None = None
        self._active = False

    @staticmethod
    def _compact_number(value: float) -> int | float:
        rounded = round(float(value), 6)
        if rounded == 0:
            return 0
        integer = round(rounded)
        return integer if abs(rounded - integer) < 1e-9 else rounded

    @staticmethod
    def _action_label(sample) -> str:
        action = sample.action_decision
        return (
            ("R" if action.right else "")
            + ("J" if action.jump else "")
        ) or "-"

    def _append_actuated_action(self, episode_id: int, sample) -> None:
        if self.trajectory_log is None:
            return
        center = self_center(sample.vision_grid)
        if center is None:
            return
        payload = {
            "e": episode_id,
            "k": "a",
            "t": sample.world_tick,
            "x": self._compact_number(center[0]),
            "y": self._compact_number(center[1]),
            "a": self._action_label(sample),
        }
        self.trajectory_log.parent.mkdir(parents=True, exist_ok=True)
        with self.trajectory_log.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(payload, separators=(",", ":"), sort_keys=True)
                + "\n"
            )

    def _append_ppo_diagnostics(
        self, episode_id: int, diagnostics: tuple[dict, ...]
    ) -> None:
        if self.trajectory_log is None or not diagnostics:
            return
        self.trajectory_log.parent.mkdir(parents=True, exist_ok=True)
        with self.trajectory_log.open("a", encoding="utf-8") as handle:
            for diagnostic in diagnostics:
                reward = diagnostic.get("rw")
                if (
                    type(reward) not in (int, float)
                    or abs(float(reward)) <= 1e-12
                ):
                    continue
                payload = {"e": episode_id, "k": "a"}
                for key, value in diagnostic.items():
                    payload[key] = (
                        self._compact_number(value)
                        if isinstance(value, float) else value
                    )
                handle.write(
                    json.dumps(payload, separators=(",", ":"), sort_keys=True)
                    + "\n"
                )

    @staticmethod
    def _send(peer: socket.socket, message: dict) -> None:
        peer.setblocking(True)
        try:
            send_model_message(peer, message)
        finally:
            peer.setblocking(False)

    def _handle_episode_end(self, peer: socket.socket, message: dict) -> None:
        if not self._active or message["episode_id"] != self._episode_id:
            raise ProtocolError("EPISODE_END does not match active episode")
        assert self._episode_id is not None
        trainable = message["trainable"] and message["result"] in {
            "success", "dead", "timeout",
        }
        if trainable and self.player.episode_mode == "train":
            updated, loss = self.player.apply_result(message["reward"])
            diagnostics = tuple(
                getattr(self.player, "last_update_diagnostics", ())
            )
        else:
            self.player.reset_episode()
            updated, loss = False, 0.0
            diagnostics = ()
        episode_id = self._episode_id
        self._append_ppo_diagnostics(episode_id, diagnostics)
        self._samples.clear()
        self._episode_id = None
        self._active = False
        self._send(peer, update_result_message(episode_id, updated, loss))

    def _handle(self, peer: socket.socket, message: dict,
                pending_observation: object | None,
                observation_matrices: bytes | None = None) -> object | None:
        message_type = message["type"]
        if message_type == PREPARE:
            if self._active:
                raise ProtocolError("PREPARE arrived before EPISODE_END")
            self.player.prepare_episode(message["mode"], message["seed"])
            self._episode_id = message["episode_id"]
            self._active = True
            self._samples.clear()
            return None
        if message_type == OBSERVE:
            if not self._active:
                return pending_observation
            return observation_from_message(message, observation_matrices)
        if message_type == ACTUATED:
            sample = self._samples.pop(message["decision_id"], None)
            if sample is not None:
                self.player.record_actuated(sample)
                if self._episode_id is not None:
                    self._append_actuated_action(self._episode_id, sample)
            return pending_observation
        if message_type == EPISODE_END:
            self._handle_episode_end(peer, message)
            return None
        if message_type == SAVE:
            raise ProtocolError("SAVE was not handled by the runtime loop")
        raise ProtocolError(f"unexpected Model runtime message {message_type}")

    def _process_pending(self, peer: socket.socket,
                         pending_observation: object | None) -> object | None:
        if pending_observation is None or not self._active:
            return pending_observation
        frame = pending_observation
        if self.inference_delay:
            time.sleep(self.inference_delay)
        sample = self.player.process_grid(frame)
        if sample is not None:
            self._decision_id += 1
            self._samples[self._decision_id] = sample
            self._send(peer, decision_message(
                self._decision_id, sample.world_tick,
                sample.action_decision.right,
                sample.action_decision.jump,
            ))
        return None

    def run(self, planner_path: str | Path, motor_path: str | Path,
            critic_path: str | Path, optimizer_path: str | Path) -> int:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.listen_host, self.listen_port))
        listener.listen(1)
        self.bound_address = listener.getsockname()[:2]
        assert self.bound_address is not None
        print("READY " + json.dumps({
            "host": self.bound_address[0], "port": self.bound_address[1],
        }, separators=(",", ":"), sort_keys=True), flush=True)
        try:
            peer, _address = listener.accept()
            with peer:
                peer.setblocking(False)
                self._send(peer, ready_message())
                reader = _FrameReader()
                pending_observation = None
                while True:
                    try:
                        messages = reader.read_available(peer)
                    except BlockingIOError:
                        messages = []
                    if not messages:
                        readable, _, _ = select.select([peer], [], [], 0.05)
                        if not readable:
                            continue
                        messages = reader.read_available(peer)
                    for message, observation_matrices in messages:
                        if message["type"] == EPISODE_END:
                            pending_observation = self._process_pending(
                                peer, pending_observation)
                        if message["type"] == SAVE:
                            if self._active:
                                raise ProtocolError("SAVE arrived during an active episode")
                            from game2.v2.player.learned.checkpoint import (
                                save_critic, save_motor_controller,
                                save_optimizer, save_planner,
                            )
                            Path(planner_path).parent.mkdir(parents=True, exist_ok=True)
                            save_planner(self.player.planner, planner_path)
                            save_motor_controller(self.player.motor_controller, motor_path)
                            save_critic(self.player.critic, critic_path)
                            assert self.player.optimizer is not None
                            save_optimizer(self.player.optimizer, optimizer_path)
                            self._send(peer, saved_message())
                            # Keep the successful worker alive until Player has
                            # consumed SAVED and closed its side of the boundary.
                            peer.setblocking(True)
                            while peer.recv(4096):
                                pass
                            return 0
                        pending_observation = self._handle(
                            peer, message, pending_observation, observation_matrices)
                    pending_observation = self._process_pending(peer, pending_observation)
        finally:
            listener.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Game2 V2 Model runtime")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=0)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--planner-seed", type=int, default=1)
    parser.add_argument("--motor-seed", type=int, default=2)
    parser.add_argument("--critic-seed", type=int, default=3)
    parser.add_argument("--planner-checkpoint")
    parser.add_argument("--motor-checkpoint")
    parser.add_argument("--critic-checkpoint")
    parser.add_argument("--optimizer-checkpoint")
    parser.add_argument("--checkpoint-dir")
    parser.add_argument("--trajectory-log")
    parser.add_argument("--inference-delay", type=float, default=0.0)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.checkpoint_dir:
            planner_path, motor_path, critic_path, optimizer_path = _checkpoint_paths(
                args.checkpoint_dir
            )
            if not args.fresh and args.planner_checkpoint is None:
                args.planner_checkpoint = planner_path
                args.motor_checkpoint = motor_path
                args.critic_checkpoint = critic_path
                args.optimizer_checkpoint = optimizer_path
        elif not args.fresh and (
            args.planner_checkpoint is None
            or args.motor_checkpoint is None
            or args.critic_checkpoint is None
            or args.optimizer_checkpoint is None
        ):
            raise ValueError(
                "Model resume requires planner, motor, critic, and optimizer checkpoints"
            )
        planner_path, motor_path, critic_path, optimizer_path = _checkpoint_paths(
            args.checkpoint_dir or "runtime/checkpoints"
        )
        if args.planner_checkpoint is not None:
            planner_path = Path(args.planner_checkpoint)
        if args.motor_checkpoint is not None:
            motor_path = Path(args.motor_checkpoint)
        if args.critic_checkpoint is not None:
            critic_path = Path(args.critic_checkpoint)
        if args.optimizer_checkpoint is not None:
            optimizer_path = Path(args.optimizer_checkpoint)
        player = build_model(
            fresh=args.fresh,
            planner_seed=args.planner_seed,
            motor_seed=args.motor_seed,
            critic_seed=args.critic_seed,
            planner_checkpoint=args.planner_checkpoint,
            motor_checkpoint=args.motor_checkpoint,
            critic_checkpoint=args.critic_checkpoint,
            optimizer_checkpoint=args.optimizer_checkpoint,
        )
        return ModelRuntime(
            player,
            listen_host=args.listen_host,
            listen_port=args.listen_port,
            inference_delay=args.inference_delay,
            trajectory_log=args.trajectory_log,
        ).run(planner_path, motor_path, critic_path, optimizer_path)
    except (EOFError, OSError, RuntimeError, TypeError, ValueError, ConnectionError) as exc:
        print(f"ERROR Model runtime failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
