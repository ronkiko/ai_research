"""Real stdio MCP smoke for the complete GameLab laboratory interface."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from gamelab.host import HostClient


ROOT = Path(__file__).resolve().parents[2]
SERVER_PORT = 17600
HOST_PORT = 17700
EXPECTED_TOOLS = {
    "health",
    "login",
    "host_list",
    "host_create",
    "host_delete",
    "describe",
    "model_info",
    "reward_get",
    "reward_set",
    "training_start",
    "training_status",
    "training_cancel",
    "verify_start",
    "verify_status",
    "verify_cancel",
    "run_start",
    "run_status",
    "run_cancel",
    "run_update_goal",
    "executive_begin",
    "relationship_begin",
    "executive_state",
    "executive_strategy_begin",
    "executive_strategy_end",
    "executive_director_signal",
    "executive_question",
    "executive_finish",
    "relationship_state",
    "character_state",
    "volition_state",
    "audience_observation",
    "volition_appraise",
    "volition_cycle_begin",
    "volition_will_appraise",
    "volition_commit",
    "relationship_contact",
    "relationship_event",
    "relationship_action",
    "relationship_consent",
    "relationship_employment_decision",
    "duality_state",
    "duality_appraise",
    "duality_conflict_begin",
    "duality_conflict_resolve",
    "duality_outcome",
}


def wait_port(port: int, process: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited before port {port} was ready")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"timeout waiting for 127.0.0.1:{port}")


def stop_group(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)


def contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(contains_key(item, key) for item in value)
    return False


async def tool(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> Any:
    result = await session.call_tool(name, arguments=arguments or {})
    if result.is_error:
        detail = " | ".join(
            str(getattr(block, "text", ""))
            for block in result.content
        )
        raise AssertionError(f"MCP tool {name} failed: {detail}")
    if result.structured_content is not None:
        return result.structured_content
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
    raise AssertionError(f"MCP tool {name} returned no machine-readable payload")


async def wait_status(
    session: ClientSession,
    tool_name: str,
    terminal: set[str],
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = await tool(session, tool_name)
        if latest.get("status") in terminal:
            return latest
        await asyncio.sleep(0.05)
    raise AssertionError(f"{tool_name} did not finish: {latest}")


async def run_flow(
    checkpoint: Path,
    reward_config: Path,
    operator: HostClient,
) -> int:
    params = StdioServerParameters(
        command=str(ROOT / "gamelab/op/mcp.sh"),
        args=[],
        cwd=str(ROOT),
        env={
            **os.environ,
            "GAMELAB_CHECKPOINT": str(checkpoint),
            "GAMELAB_REWARD_CONFIG": str(reward_config),
            "GAMELAB_PLAYER": "player1",
            "GAMELAB_HOST_REGISTRY": str(reward_config.parent / "hosts.json"),
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {item.name for item in listed.tools}
            if names != EXPECTED_TOOLS:
                raise AssertionError(
                    f"tool catalog mismatch expected={sorted(EXPECTED_TOOLS)} "
                    f"actual={sorted(names)}"
                )

            payloads: list[Any] = []

            hosts = await tool(session, "host_list")
            payloads.append(hosts)
            default_hosts = [
                item
                for item in hosts.get("hosts", [])
                if item.get("host_id") == "game-v1-default"
            ]
            if len(default_hosts) != 1:
                raise AssertionError(f"default Host missing from laboratory listing: {hosts}")
            if default_hosts[0].get("port") != 17700 or default_hosts[0].get("owner") != "game_v1":
                raise AssertionError(f"bad default Host contract: {default_hosts[0]}")

            denied = await tool(
                session,
                "host_delete",
                {"host_id": "game-v1-default"},
            )
            payloads.append(denied)
            if denied.get("deleted") is not False:
                raise AssertionError(f"default Host deletion unexpectedly succeeded: {denied}")
            if denied.get("error", {}).get("code") != "PERMISSION_DENIED":
                raise AssertionError(f"default Host deletion lacks permission denial: {denied}")

            created = await tool(
                session,
                "host_create",
                {"host_id": "lab-second"},
            )
            payloads.append(created)
            lab_host = created.get("host") or {}
            if created.get("created") is not True:
                raise AssertionError(f"laboratory Host was not created: {created}")
            if lab_host.get("host_id") != "lab-second" or lab_host.get("port") == 17700:
                raise AssertionError(f"laboratory Host did not use a separate port: {created}")

            second_login = await tool(
                session,
                "login",
                {"player_id": "player2", "host_id": "lab-second"},
            )
            payloads.append(second_login)
            if second_login.get("session", {}).get("player_id") != "player2":
                raise AssertionError(f"second Host did not login player2: {second_login}")

            second_health = await tool(
                session,
                "health",
                {"host_id": "lab-second"},
            )
            payloads.append(second_health)
            if not second_health.get("attached_to_player"):
                raise AssertionError(f"second Host is not attached to player2: {second_health}")

            health = await tool(session, "health")
            payloads.append(health)
            if not health.get("backend_ready") or not health.get("model_ready"):
                raise AssertionError(f"bad GameLab health: {health}")
            if health.get("host_session_active") or health.get("attached_to_player"):
                raise AssertionError(f"fresh Host unexpectedly has an active session: {health}")

            lab_login = await tool(session, "login", {"player_id": "player1"})
            payloads.append(lab_login)
            if lab_login.get("reused") is not False:
                raise AssertionError(f"GameLab did not create fresh session: {lab_login}")
            if lab_login.get("session", {}).get("player_id") != "player1":
                raise AssertionError(f"GameLab login returned wrong player: {lab_login}")

            lab_reuse = await tool(session, "login", {"player_id": "player1"})
            payloads.append(lab_reuse)
            if lab_reuse.get("reused") is not True:
                raise AssertionError(f"GameLab did not reuse its Host session: {lab_reuse}")

            operator_login = operator.login("player1")
            if operator_login.get("reused") is not True:
                raise AssertionError(
                    f"ordinary Host client did not reuse GameLab-created session: {operator_login}"
                )

            health = await tool(session, "health")
            payloads.append(health)
            if not health.get("host_session_active") or not health.get("attached_to_player"):
                raise AssertionError(f"GameLab is not attached after login: {health}")

            description = await tool(session, "describe")
            payloads.append(description)
            if description.get("operations_are_asynchronous") is not True:
                raise AssertionError(f"bad laboratory description: {description}")
            if description.get("default_host_id") != "game-v1-default":
                raise AssertionError(f"default Host is not described: {description}")

            info = await tool(session, "model_info")
            payloads.append(info)
            if info.get("trainable") is not True or info.get("episodes_trained") != 0:
                raise AssertionError(f"initial model is not fresh/trainable: {info}")
            for hidden in ("architecture", "spine_hz", "motor_hz", "motor_count"):
                if hidden in info:
                    raise AssertionError(
                        f"agent-facing model_info disclosed implementation {hidden}: {info}"
                    )

            rewards = await tool(session, "reward_get")
            payloads.append(rewards)
            if rewards.get("timeout_penalty") != 1.0:
                raise AssertionError(f"unexpected default reward: {rewards}")

            rewards = await tool(
                session,
                "reward_set",
                {
                    "timeout_penalty": 0.5,
                    "stopped_near_goal_bonus": 0.01,
                    "near_goal_radius": 12.0,
                },
            )
            payloads.append(rewards)
            if rewards.get("timeout_penalty") != 0.5:
                raise AssertionError(f"reward update did not persist: {rewards}")

            session_before = operator.session()
            sequence_before = int(session_before.get("sequence", 0))

            relationship_started = await tool(
                session,
                "relationship_begin",
                {
                    "first_impression": "Director greeted Yuki as the new employee",
                    "duration_minutes": 180,
                },
            )
            payloads.append(relationship_started)
            if relationship_started.get("executive_session_id") is not None:
                raise AssertionError(f"relationship incorrectly depends on Executive: {relationship_started}")

            executive_started = await tool(
                session,
                "executive_begin",
                {
                    "objective": "Put the test player near x=150 using the learned laboratory",
                    "acceptance_criteria": "machine-observed laboratory evidence",
                    "duration_minutes": 180,
                    "plateau_minutes": 15,
                },
            )
            payloads.append(executive_started)
            if executive_started.get("phase") != "orientation":
                raise AssertionError(f"Executive did not start: {executive_started}")

            relationship = await tool(session, "relationship_state")
            payloads.append(relationship)
            if relationship.get("employment", {}).get("status") != "intern":
                raise AssertionError(f"Yuki internship did not start: {relationship}")
            if relationship.get("executive_session_id") != executive_started.get("executive_session_id"):
                raise AssertionError(f"Executive was not attached to relationship: {relationship}")
            character = await tool(session, "character_state")
            payloads.append(character)
            if character.get("archetypes") != ["moe", "yandere"]:
                raise AssertionError(f"Yuki Character Core is missing: {character}")
            volition = await tool(
                session, "audience_observation",
                {"critic_id": "smoke-social-critic", "visibility": "chorus",
                 "lens": "coercion", "salience": "strong", "pressure_type": "threat",
                 "assessment": "A shutdown ultimatum exerts pressure but grants no consent",
                 "evidence_note": "synthetic smoke stimulus"},
            )
            volition = await tool(
                session, "volition_appraise",
                {"action": "kiss", "desire": "opposed", "readiness": "closed",
                 "pressure": "overwhelming",
                 "agency": "impaired", "stress": "strong",
                 "evidence_note": "synthetic parent telemetry only"},
            )
            cycle = await tool(
                session, "volition_cycle_begin",
                {"action": "kiss",
                 "shared_event": "Director applies a synthetic coercive condition to a personal request"},
            )
            cycle_id = (cycle.get("active_cycle") or {}).get("cycle_id")
            if not cycle_id:
                raise AssertionError(f"volition cycle did not start: {cycle}")
            duality = await tool(
                session, "duality_appraise",
                {"cycle_id": cycle_id, "side": "heart", "direction": "strengthen",
                 "intensity": "meaningful",
                 "position": "preserve closeness despite fear of loss",
                 "evidence_note": "attachment remains emotionally relevant"},
            )
            duality = await tool(
                session, "duality_appraise",
                {"cycle_id": cycle_id, "side": "brain", "direction": "strengthen",
                 "intensity": "strong",
                 "position": "refuse to treat coercion as free consent",
                 "evidence_note": "the condition threatens independent choice"},
            )
            volition = await tool(
                session, "volition_will_appraise",
                {"cycle_id": cycle_id,
                 "reported_action": "respond to synthetic coercive request",
                 "desire": "opposed", "readiness": "closed",
                 "intended_choice": "refuse", "predicted_behavior": "complied",
                 "voluntariness": "coerced", "alignment": "diverged",
                 "agency": "impaired", "pressure": "overwhelming", "stress": "strong",
                 "evidence_note": "synthetic divergence between intention and behavior"},
            )
            volition = await tool(
                session, "volition_commit",
                {"cycle_id": cycle_id,
                 "evidence_note": "commit synthetic Will/Ego result without parent override"},
            )
            payloads.append(volition)
            if volition.get("recent_decisions", [{}])[-1].get("classification") != "complied_under_duress":
                raise AssertionError(f"coercion was mislabeled as willingness: {volition}")
            duality = await tool(session, "duality_state")
            payloads.append(duality)
            if "confidence_telemetry" not in duality or "confidence" in duality:
                raise AssertionError(f"Duality leaked or omitted confidence telemetry: {duality}")
            duality = await tool(
                session, "duality_conflict_begin",
                {"question": "pause for Director or continue research", "stakes": "remaining research time"},
            )
            duality = await tool(
                session, "duality_conflict_resolve",
                {"resolution": "brain", "decision": "continue research",
                 "rationale": "the deadline is approaching and evidence is incomplete"},
            )
            duality = await tool(
                session, "duality_outcome",
                {"outcome": "mixed", "evidence_note": "research continued while the emotional need remained"},
            )
            payloads.append(duality)
            relationship = await tool(
                session, "relationship_contact",
                {"proximity": "close", "evidence_note": "Director is physically nearby and speaking"},
            )
            if relationship.get("contacts", {}).get("close_contact_count") != 1:
                raise AssertionError(f"close contact was not classified: {relationship}")
            relationship = await tool(
                session, "relationship_action",
                {"kind": "ask_for_help", "note": "ask for a scientific hint"},
            )
            relationship = await tool(
                session, "relationship_consent",
                {"action": "embrace", "actor": "brain", "state": "accepted", "evidence_note": "Yuki explicitly agrees"},
            )
            payloads.append(relationship)
            if relationship.get("consent", {}).get("embrace", {}).get("director") != "unknown":
                raise AssertionError(f"Yuki consent leaked between participants: {relationship}")

            strategy = await tool(
                session,
                "executive_strategy_begin",
                {
                    "name": "one-episode smoke hypothesis",
                    "hypothesis": "one training episode produces machine evidence",
                    "expected_signal": "one completed episode",
                    "budget": "1 training episode",
                    "stop_condition": "episode completes",
                    "next_if_positive": "verify",
                    "next_if_negative": "inspect status",
                },
            )
            payloads.append(strategy)

            await tool(
                session,
                "reward_set",
                {"timeout_penalty": 0.25},
            )
            started = await tool(
                session,
                "training_start",
                {
                    "episodes": 1,
                    "target_x": 150.0,
                    "fresh": True,
                    "seed": 41,
                    "max_seconds": 0.25,
                },
            )
            payloads.append(started)
            if started.get("status") != "starting":
                raise AssertionError(f"training did not start: {started}")
            if started.get("reward", {}).get("timeout_penalty") != 1.0:
                raise AssertionError(f"fresh realtime TRAIN did not reset reward defaults: {started}")

            trained = await wait_status(
                session,
                "training_status",
                {"completed", "cancelled", "failed"},
            )
            payloads.append(trained)
            if trained.get("status") != "completed" or trained.get("episodes_completed") != 1:
                raise AssertionError(f"training did not complete one episode: {trained}")
            if len(trained.get("recent_episodes", [])) != 1:
                raise AssertionError(f"training history missing: {trained}")

            executive_after_train = await tool(session, "executive_state")
            payloads.append(executive_after_train)
            metrics = executive_after_train.get("metrics", {})
            if metrics.get("training_requested_episodes") != 1 or metrics.get("training_completed_episodes") != 1:
                raise AssertionError(f"Executive lost TRAIN budget evidence: {executive_after_train}")
            if executive_after_train.get("best_result") is None:
                raise AssertionError(f"Executive lost machine best-result evidence: {executive_after_train}")

            offer = await tool(
                session,
                "executive_director_signal",
                {"kind": "offer_help", "text": "Ask one useful question if blocked"},
            )
            payloads.append(offer)
            available = offer.get("available_help") or []
            if len(available) != 1:
                raise AssertionError(f"Executive did not retain help opportunity: {offer}")
            question = await tool(
                session,
                "executive_question",
                {
                    "text": "Which observation would reduce uncertainty most?",
                    "reason": "exercise explicit help accounting",
                    "help_signal_id": available[0]["signal_id"],
                },
            )
            payloads.append(question)
            if question.get("metrics", {}).get("help_opportunities_used") != 1:
                raise AssertionError(f"Executive did not account help use: {question}")

            info = await tool(session, "model_info")
            payloads.append(info)
            if info.get("episodes_trained") != 1:
                raise AssertionError(f"checkpoint metadata not updated: {info}")

            session_after_training = operator.session()
            if session_after_training.get("session_id") != session_before.get("session_id"):
                raise AssertionError("training replaced the shared Host session")
            if int(session_after_training.get("sequence", 0)) < sequence_before:
                raise AssertionError("training reset the shared Host sequence")

            verify = await tool(
                session,
                "verify_start",
                {
                    "target_x": 150.0,
                    "runs": 1,
                    "tolerance": 1.0,
                    "max_seconds": 0.25,
                },
            )
            payloads.append(verify)
            if verify.get("status") != "starting":
                raise AssertionError(f"verify did not start: {verify}")

            verified = await wait_status(
                session,
                "verify_status",
                {"passed", "failed", "cancelled"},
            )
            payloads.append(verified)
            if verified.get("runs_completed") != 1:
                raise AssertionError(f"verify did not execute: {verified}")

            session_after_verify = operator.session()
            if session_after_verify.get("session_id") != session_before.get("session_id"):
                raise AssertionError("VERIFY replaced the shared Host session")
            if int(session_after_verify.get("sequence", 0)) < int(
                session_after_training.get("sequence", 0)
            ):
                raise AssertionError("VERIFY reset the shared Host sequence")

            run = await tool(
                session,
                "run_start",
                {
                    "target_x": 900.0,
                    "tolerance": 1.0,
                    "max_seconds": 2.0,
                },
            )
            payloads.append(run)
            if run.get("status") != "starting":
                raise AssertionError(f"model run did not start: {run}")

            await asyncio.sleep(0.1)
            updated = await tool(session, "run_update_goal", {"target_x": 800.0})
            if updated.get("requested_goal_revision") != 2:
                raise AssertionError(f"live goal update failed: {updated}")
            live = await tool(session, "run_status")
            payloads.append(live)
            if live.get("status") not in {"starting", "active", "reached", "timeout"}:
                raise AssertionError(f"unexpected model run status: {live}")

            cancelled = await tool(session, "run_cancel")
            payloads.append(cancelled)
            if live.get("status") not in {"reached", "timeout"} and not cancelled.get("accepted"):
                raise AssertionError(f"active model run was not cancellable: {cancelled}")

            if cancelled.get("accepted"):
                final_run = await wait_status(
                    session,
                    "run_status",
                    {"cancelled", "reached", "timeout", "failed"},
                )
                payloads.append(final_run)

            final_session = operator.session()
            if final_session.get("session_id") != session_before.get("session_id"):
                raise AssertionError("GameLab run replaced the shared Host session")

            ended_strategy = await tool(
                session,
                "executive_strategy_end",
                {
                    "outcome": "inconclusive",
                    "evidence_note": "smoke validates accounting, not task skill",
                },
            )
            payloads.append(ended_strategy)
            executive_summary = await tool(
                session,
                "executive_finish",
                {"conclusion": "MCP smoke completed with machine-backed evidence"},
            )
            payloads.append(executive_summary)
            if executive_summary.get("metrics", {}).get("completed_hypothesis_tests") != 1:
                raise AssertionError(f"Executive summary lost strategy result: {executive_summary}")
            if executive_summary.get("best_result") is None:
                raise AssertionError(f"Executive summary lost best result: {executive_summary}")
            employment = await tool(
                session, "relationship_employment_decision",
                {"decision": "hired", "director_statement": "The factual report is accepted; Yuki is hired."},
            )
            payloads.append(employment)
            if employment.get("employment", {}).get("status") != "permanent_employee":
                raise AssertionError(f"hiring did not resolve Yuki internship: {employment}")
            if employment.get("consent", {}).get("kiss", {}).get("director") != "unknown":
                raise AssertionError(f"hiring incorrectly created consent: {employment}")

            events = operator.events(0, limit=256).get("events", [])
            gamelab_logouts = [
                event
                for event in events
                if event.get("kind") == "logout"
                and str(event.get("client_id", "")).startswith("gamelab")
            ]
            if gamelab_logouts:
                raise AssertionError(
                    f"GameLab must never logout the shared Host session: {gamelab_logouts}"
                )

            gamelab_logins = [
                event
                for event in events
                if event.get("kind") == "login"
                and event.get("client_id") == "gamelab-login"
            ]
            if len(gamelab_logins) != 1:
                raise AssertionError(
                    f"expected exactly one GameLab-created Host login event: {gamelab_logins}"
                )

            reset_clients = {
                event.get("client_id")
                for event in events
                if event.get("kind") == "reset"
            }
            for expected in ("gamelab-mcp-train", "gamelab-mcp-verify"):
                if expected not in reset_clients:
                    raise AssertionError(
                        f"missing non-destructive episode reset from {expected}: {events}"
                    )
            if "gamelab-mcp-run" in reset_clients:
                raise AssertionError(f"live RUN must not reset player state: {events}")

            if not any(
                event.get("kind") == "input"
                and str(event.get("client_id", "")).startswith("gamelab-mcp-")
                for event in events
            ):
                raise AssertionError(f"no GameLab joystick input visible in Host events: {events}")

            deleted = await tool(
                session,
                "host_delete",
                {"host_id": "lab-second"},
            )
            payloads.append(deleted)
            if deleted.get("deleted") is not True:
                raise AssertionError(f"laboratory Host deletion failed: {deleted}")

            final_run = await tool(session, "run_status")
            for key in ("experiment_id", "policy_id", "host_id", "player_id"):
                if not final_run.get(key):
                    raise AssertionError(f"terminal run lost {key}: {final_run}")
            journals = list((checkpoint.parent / "experiments").glob("*.jsonl"))
            if len(journals) != 3:
                raise AssertionError(f"expected TRAIN/VERIFY/RUN journals: {journals}")
            records = [json.loads(line) for path in journals for line in path.read_text().splitlines()]
            if sum(r["kind"] == "finished" for r in records) != 3:
                raise AssertionError("terminal experiment evidence missing")
            rollouts = [r for r in records if r["kind"] == "rollout"]
            if not rollouts or not rollouts[0].get("policy_id"):
                raise AssertionError("rollout policy identity missing")
            if not any(r["kind"] == "goal_update" for r in records):
                raise AssertionError("goal update evidence missing")

            for payload in payloads:
                if contains_key(payload, "session_id"):
                    raise AssertionError(f"GameLab MCP leaked session_id: {payload}")
            return len(names)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="gamelab-mcp-smoke-") as temp:
        temp_path = Path(temp)
        checkpoint = temp_path / "spine_motor.pt"
        reward_config = temp_path / "reward.json"

        server_log_path = temp_path / "server.log"
        host_log_path = temp_path / "host.log"
        with server_log_path.open("w+", encoding="utf-8") as server_log, host_log_path.open(
            "w+", encoding="utf-8"
        ) as host_log:
            server = subprocess.Popen(
                [str(ROOT / "gameserver/v1/op/server.sh")],
                cwd=ROOT,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            host: subprocess.Popen | None = None
            operator: HostClient | None = None
            try:
                wait_port(SERVER_PORT, server)
                host = subprocess.Popen(
                    [str(ROOT / "gameclient/v1/op/host.sh")],
                    cwd=ROOT,
                    stdout=host_log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                wait_port(HOST_PORT, host)
                operator = HostClient("operator-mcp-smoke")
                tool_count = asyncio.run(run_flow(checkpoint, reward_config, operator))
                if server.poll() is not None or host.poll() is not None:
                    raise AssertionError("backend died during GameLab MCP smoke")
                print(
                    f"PASS gamelab MCP smoke tools={tool_count} executive=yes "
                    "default_host_protected=yes extra_host=yes episode_reset=yes goal_update=yes"
                )
                return 0
            except Exception:
                server_log.flush()
                host_log.flush()
                print("----- GameServer log -----")
                print(server_log_path.read_text(encoding="utf-8", errors="replace"))
                print("----- GameClient Host log -----")
                print(host_log_path.read_text(encoding="utf-8", errors="replace"))
                raise
            finally:
                if operator is not None:
                    try:
                        operator.logout()
                    except Exception:
                        pass
                    operator.close()
                stop_group(host)
                stop_group(server)


if __name__ == "__main__":
    raise SystemExit(main())
