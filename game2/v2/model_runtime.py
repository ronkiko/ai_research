"""Standalone OS process that owns learned inference, updates, and weights."""
from __future__ import annotations

import argparse
import hashlib
from copy import copy
from dataclasses import replace
import json
import select
import socket
import sys
import time
from pathlib import Path

from game2.v2.contracts.bot_profile import BotProfile
from game2.v2.learning.checkpoints import pin_checkpoint_paths
from game2.v2.contracts.framing import MAX_FRAME_SIZE, ProtocolError, decode_frame
from game2.v2.contracts.model import (
    ACTUATED,
    CONTROL_REQUESTED,
    CONTROL_RESULT,
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
from game2.v2.player.learned.contracts import (
    ActionDecision,
    ButtonCommand,
    ControlCommand,
    apply_control_command,
    gate_control_command,
)
from game2.v2.player.learned.checkpoint import (
    load_critic,
    load_motor_controller,
    load_optimizer,
    load_planner,
    save_checkpoint_set,
)
from game2.v2.player.learned.critic import CNNCritic
from game2.v2.player.learned.motor import DualMotorController
from game2.v2.player.learned.planner import CNNPlanner
from game2.v2.player.learned.registry import (
    build_motor_controller,
    build_planner,
    validate_runtime_profile,
)
from game2.v2.player.learned.runtime import (
    DecisionSample,
    LearnedPlayer,
    POLICY_STRIDE_TICKS,
)
from game2.v2.training.work import (
    DEFAULT_EPISODE_STORE,
    EpisodeDataset,
    EpisodeStore,
    train_episode,
)


def _checkpoint_paths(directory: str | Path) -> tuple[Path, Path, Path, Path]:
    root = Path(directory)
    return (
        root / "planner.pt",
        root / "motor.pt",
        root / "critic.pt",
        root / "optimizer.pt",
    )


def build_model(*, fresh: bool, profile: BotProfile | None = None,
                planner_seed: int = 1, motor_seed: int = 2, critic_seed: int = 3,
                planner_checkpoint: str | Path | None = None,
                motor_checkpoint: str | Path | None = None,
                critic_checkpoint: str | Path | None = None,
                optimizer_checkpoint: str | Path | None = None) -> LearnedPlayer:
    if profile is not None:
        validate_runtime_profile(profile)
    checkpoints = (
        planner_checkpoint is not None,
        motor_checkpoint is not None,
        critic_checkpoint is not None,
        optimizer_checkpoint is not None,
    )
    if fresh:
        if any(checkpoints):
            raise ValueError("Fresh Model runtime cannot use checkpoints")
        if profile is None:
            planner = CNNPlanner.fresh(planner_seed)
            motor = DualMotorController.fresh(motor_seed)
        else:
            planner = build_planner(profile)
            motor = build_motor_controller(profile)
        critic = CNNCritic.fresh(critic_seed, planner.backbone)
    else:
        if checkpoints != (True, True, True, True):
            raise ValueError(
                "Model resume requires planner, motor, critic, and optimizer checkpoints"
            )
        assert planner_checkpoint is not None
        assert motor_checkpoint is not None
        assert critic_checkpoint is not None
        assert optimizer_checkpoint is not None
        planner_checkpoint, motor_checkpoint, critic_checkpoint, optimizer_checkpoint = (
            pin_checkpoint_paths((planner_checkpoint, motor_checkpoint,
                                  critic_checkpoint, optimizer_checkpoint))
        )
        planner = load_planner(planner_checkpoint)
        motor = load_motor_controller(motor_checkpoint)
        critic = load_critic(critic_checkpoint)
        planner_backbone = planner.backbone.state_dict()
        critic_backbone = critic.backbone.state_dict()
        if planner_backbone.keys() != critic_backbone.keys() or any(
            not planner_backbone[key].equal(critic_backbone[key])
            for key in planner_backbone
        ):
            raise ValueError(
                "planner and critic checkpoints contain different shared backbones"
            )
        critic.backbone = planner.backbone
    player = LearnedPlayer(planner, motor, critic)
    player.bot_profile = profile
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
                 episode_store: str | Path = DEFAULT_EPISODE_STORE):
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
        self.episode_store = EpisodeStore(episode_store)
        self._episode_dataset: EpisodeDataset | None = None
        self.bound_address: tuple[str, int] | None = None
        self._decision_id = 0
        self._samples: dict[int, object] = {}
        self._episode_id: int | None = None
        self._active = False
        self._control_tick: int | None = None
        self._control_used_buttons: set[str] = set()
        self._control_tick_state: ActionDecision | None = None
        self._checkpoint_paths: tuple[Path, Path, Path, Path] | None = None
        self._episode_decision_count = 0
        self._episode_actuated_count = 0
        self._episode_observations_received = 0
        self._episode_dropped_observations = 0

    def _save_checkpoints(self) -> str:
        if self._checkpoint_paths is None:
            raise RuntimeError("checkpoint paths are not configured")
        paths = save_checkpoint_set(self.player, self._checkpoint_paths)

        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.name.encode("utf-8"))
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sample_pad_state(sample) -> ActionDecision:
        pad_right = getattr(sample, "pad_right", None)
        pad_jump = getattr(sample, "pad_jump", None)
        if type(pad_right) is bool and type(pad_jump) is bool:
            return ActionDecision(pad_right, pad_jump)
        desired = getattr(sample, "desired_state", None)
        command = getattr(sample, "action_decision", None)
        if isinstance(desired, ActionDecision) and isinstance(command, ControlCommand):
            return desired
        raise TypeError("Model sample does not expose its controller base state")

    @staticmethod
    def _replace_control_result(
        sample, desired_state: ActionDecision, suppressed_buttons: tuple[str, ...]
    ):
        if isinstance(sample, DecisionSample):
            return replace(
                sample,
                desired_state=desired_state,
                suppressed_buttons=suppressed_buttons,
            )
        updated = copy(sample)
        updated.desired_state = desired_state
        updated.suppressed_buttons = suppressed_buttons
        return updated

    def _gate_control_request(self, sample) -> tuple[object, ControlCommand]:
        tick = sample.world_tick
        if type(tick) is not int or tick < 0:
            raise ProtocolError("Model control request has invalid world_tick")
        if self._control_tick is None or tick > self._control_tick:
            self._control_tick = tick
            self._control_used_buttons.clear()
            self._control_tick_state = self._sample_pad_state(sample)
        elif tick < self._control_tick:
            raise ProtocolError("Model control request moved backwards in world_tick")
        if self._control_tick_state is None:
            self._control_tick_state = self._sample_pad_state(sample)

        requested = sample.action_decision
        if not isinstance(requested, ControlCommand):
            raise TypeError("Model policy must return a ControlCommand")
        desired, applied, suppressed = gate_control_command(
            self._control_tick_state, requested
        )
        self._control_tick_state = desired
        sample = self._replace_control_result(sample, desired, suppressed)
        return sample, applied

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
        dataset = self._episode_dataset
        if dataset is None:
            raise ProtocolError("EPISODE_END has no active EpisodeDataset")
        dataset.finalize(
            result=message["result"],
            finish_world_tick=message["finish_world_tick"],
            terminal_reward=message["reward"],
            trainable=trainable,
        )
        metrics: dict[str, object] = {}
        if trainable and self.player.episode_mode == "train":
            training = train_episode(self.player, dataset)
            updated, loss = training.updated, training.loss
            metrics.update(training.metrics)
        else:
            updated, loss = False, 0.0
            metrics.update({
                "rollout_records": len(dataset.steps()),
                "ppo_records": 0,
                "optimizer_steps": 0,
            })
        self.player.reset_episode()
        self.episode_store.rotate()
        metrics["policy_stride_ticks"] = POLICY_STRIDE_TICKS
        metrics["decision_count"] = self._episode_decision_count
        metrics["actuated_count"] = self._episode_actuated_count
        metrics["model_observations_received"] = self._episode_observations_received
        metrics["model_dropped_observations"] = self._episode_dropped_observations
        metrics["checkpoint_saved"] = False
        metrics["checkpoint_hash"] = ""
        if (
            updated
            and self._checkpoint_paths is not None
            and isinstance(self.player, LearnedPlayer)
        ):
            metrics["checkpoint_hash"] = self._save_checkpoints()
            metrics["checkpoint_saved"] = True
        episode_id = self._episode_id
        dataset.update_training_summary(
            updated=updated, loss=loss, metrics=metrics
        )
        self._samples.clear()
        self._episode_dataset = None
        self._episode_id = None
        self._active = False
        self._control_tick = None
        self._control_used_buttons.clear()
        self._control_tick_state = None
        self._episode_decision_count = 0
        self._episode_actuated_count = 0
        self._episode_observations_received = 0
        self._episode_dropped_observations = 0
        self._send(peer, update_result_message(episode_id, updated, loss, metrics))

    def _handle(self, peer: socket.socket, message: dict,
                pending_observation: object | None,
                observation_matrices: bytes | None = None) -> object | None:
        message_type = message["type"]
        if message_type == PREPARE:
            if self._active:
                raise ProtocolError("PREPARE arrived before EPISODE_END")
            self.player.prepare_episode(message["mode"], message["seed"])
            self._episode_dataset = self.episode_store.create(
                episode_id=message["episode_id"],
                mode=message["mode"],
                source="realtime",
                seed=message["seed"],
                policy_stride_ticks=POLICY_STRIDE_TICKS,
            )
            self._episode_id = message["episode_id"]
            self._active = True
            self._samples.clear()
            self._control_tick = None
            self._control_used_buttons.clear()
            self._control_tick_state = None
            self._episode_decision_count = 0
            self._episode_actuated_count = 0
            self._episode_observations_received = 0
            self._episode_dropped_observations = 0
            return None
        if message_type == OBSERVE:
            if not self._active:
                return pending_observation
            self._episode_observations_received += 1
            if pending_observation is not None:
                self._episode_dropped_observations += 1
            return observation_from_message(message, observation_matrices)
        if message_type == CONTROL_REQUESTED:
            sample = self._samples.get(message["decision_id"])
            if sample is not None and self._episode_dataset is not None:
                self._episode_dataset.mark_control_requested(
                    sample.policy_sequence
                )
            return pending_observation
        if message_type == CONTROL_RESULT:
            sample = self._samples.get(message["decision_id"])
            if sample is not None and self._episode_dataset is not None:
                self._episode_dataset.mark_control_result(
                    sample.policy_sequence, message["status"]
                )
            return pending_observation
        if message_type == ACTUATED:
            sample = self._samples.pop(message["decision_id"], None)
            if sample is not None:
                self._episode_actuated_count += 1
                self.player.record_actuated(sample)
                if self._episode_dataset is not None:
                    self._episode_dataset.upsert_sample(
                        sample,
                        duration_ticks=POLICY_STRIDE_TICKS,
                        actuated=True,
                    )
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
            self._episode_decision_count += 1
            if self._episode_dataset is None:
                raise RuntimeError("Model decision has no active EpisodeDataset")
            self._episode_dataset.upsert_sample(
                sample, duration_ticks=POLICY_STRIDE_TICKS
            )
            command = sample.action_decision
            if not isinstance(command, ControlCommand):
                raise TypeError("Model policy must return a ControlCommand")
            if not command.any:
                return None
            sample, applied_command = self._gate_control_request(sample)
            self._episode_dataset.upsert_sample(
                sample, duration_ticks=POLICY_STRIDE_TICKS
            )
            if hasattr(self.player, "latest_sample"):
                self.player.latest_sample = sample
            if hasattr(self.player, "latest_decision"):
                self.player.latest_decision = sample.desired_state
            if not applied_command.any:
                return None
            desired_state = sample.desired_state
            if not isinstance(desired_state, ActionDecision):
                raise TypeError("Model action must resolve to an ActionDecision")
            self._decision_id += 1
            self._samples[self._decision_id] = sample
            self._send(peer, decision_message(
                self._decision_id, sample.world_tick,
                desired_state.right,
                desired_state.jump,
            ))
        return None

    def run(self, planner_path: str | Path, motor_path: str | Path,
            critic_path: str | Path, optimizer_path: str | Path) -> int:
        self._checkpoint_paths = (
            Path(planner_path),
            Path(motor_path),
            Path(critic_path),
            Path(optimizer_path),
        )
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
                        if message["type"] == SAVE:
                            if self._active:
                                raise ProtocolError("SAVE arrived during an active episode")
                            self._save_checkpoints()
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
    parser.add_argument("--profile")
    parser.add_argument("--planner-seed", type=int, default=1)
    parser.add_argument("--motor-seed", type=int, default=2)
    parser.add_argument("--critic-seed", type=int, default=3)
    parser.add_argument("--planner-checkpoint")
    parser.add_argument("--motor-checkpoint")
    parser.add_argument("--critic-checkpoint")
    parser.add_argument("--optimizer-checkpoint")
    parser.add_argument("--checkpoint-dir")
    parser.add_argument("--episode-store", default=str(DEFAULT_EPISODE_STORE))
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
        profile = BotProfile.from_file(args.profile) if args.profile else None
        player = build_model(
            fresh=args.fresh,
            profile=profile,
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
            episode_store=args.episode_store,
        ).run(planner_path, motor_path, critic_path, optimizer_path)
    except (EOFError, OSError, RuntimeError, TypeError, ValueError, ConnectionError) as exc:
        print(f"ERROR Model runtime failed: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
