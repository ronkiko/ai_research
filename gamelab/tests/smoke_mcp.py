"""Real stdio MCP smoke for the LLM goal-level GameLab interface."""
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

from gamelab.models import SpineMotorPolicy, save_checkpoint


ROOT = Path(__file__).resolve().parents[2]
SERVER_PORT = 17600
HOST_PORT = 17700
EXPECTED_TOOLS = {"health", "model_info", "set_goal", "goal_status", "cancel_goal"}


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


async def run_flow(checkpoint: Path) -> None:
    params = StdioServerParameters(
        command=str(ROOT / "gamelab/op/mcp.sh"),
        args=[],
        cwd=str(ROOT),
        env={
            **os.environ,
            "GAMELAB_CHECKPOINT": str(checkpoint),
            "GAMELAB_PLAYER": "player1",
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

            health = await tool(session, "health")
            if not health.get("backend_ready") or not health.get("model_ready"):
                raise AssertionError(f"bad GameLab health: {health}")

            info = await tool(session, "model_info")
            if info.get("motor_count") != 1 or info.get("procedural_controller") is not False:
                raise AssertionError(f"bad model contract: {info}")

            started = await tool(
                session,
                "set_goal",
                {"target_x": 987.0, "tolerance": 1.0, "max_seconds": 2.0},
            )
            if started.get("status") != "starting":
                raise AssertionError(f"goal did not start: {started}")

            await asyncio.sleep(0.15)
            status = await tool(session, "goal_status")
            if status.get("status") not in {"starting", "active", "reached"}:
                raise AssertionError(f"unexpected goal status: {status}")

            cancelled = await tool(session, "cancel_goal")
            if status.get("status") != "reached" and not cancelled.get("accepted"):
                raise AssertionError(f"active goal was not cancellable: {cancelled}")

            for payload in (health, info, started, status, cancelled):
                if contains_key(payload, "session_id"):
                    raise AssertionError(f"GameLab MCP leaked session_id: {payload}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="gamelab-mcp-smoke-") as temp:
        temp_path = Path(temp)
        checkpoint = temp_path / "spine_motor.pt"
        save_checkpoint(checkpoint, SpineMotorPolicy.fresh(37))

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
                asyncio.run(run_flow(checkpoint))
                if server.poll() is not None or host.poll() is not None:
                    raise AssertionError("backend died during GameLab MCP smoke")
                print("PASS gamelab MCP goal smoke tools=5 no_session_leak=yes")
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
                stop_group(host)
                stop_group(server)


if __name__ == "__main__":
    raise SystemExit(main())
