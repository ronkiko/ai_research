"""Real stdio MCP smoke against real GameServer and GameClient Host."""
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


ROOT = Path(__file__).resolve().parents[3]
SERVER_PORT = 17600
HOST_PORT = 17700
EXPECTED_TOOLS = {
    "health",
    "describe",
    "game_state",
    "players",
    "login",
    "session",
    "move",
    "recent_events",
    "logout",
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
        text = " | ".join(
            str(getattr(block, "text", ""))
            for block in result.content
        )
        raise AssertionError(f"MCP tool {name} failed: {text}")
    if result.structured_content is not None:
        return result.structured_content
    for block in result.content:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"MCP tool {name} returned no machine-readable payload")


async def run_mcp_flow() -> None:
    params = StdioServerParameters(
        command=str(ROOT / "gameclient/v1/op/mcp.sh"),
        args=[],
        cwd=str(ROOT),
        env={
            **os.environ,
            "GAMECLIENT_HOST": "127.0.0.1",
            "GAMECLIENT_PORT": str(HOST_PORT),
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listed = await session.list_tools()
            names = {item.name for item in listed.tools}
            if names != EXPECTED_TOOLS:
                raise AssertionError(
                    f"MCP tool catalog mismatch expected={sorted(EXPECTED_TOOLS)} "
                    f"actual={sorted(names)}"
                )

            health = await tool(session, "health")
            if health.get("status") != "ready":
                raise AssertionError(f"bad health: {health}")
            if health.get("host_id") != "game-v1-default":
                raise AssertionError(f"bad default Host identity: {health}")

            describe = await tool(session, "describe")
            if describe.get("entity") != "GameClient Host":
                raise AssertionError(f"bad describe: {describe}")
            if describe.get("host_id") != "game-v1-default":
                raise AssertionError(f"describe lacks default Host identity: {describe}")

            players = await tool(session, "players")
            if players.get("result") != ["player1", "player2", "player3"]:
                raise AssertionError(f"bad players: {players}")

            login = await tool(session, "login", {"player_id": "player1"})
            if contains_key(login, "session_id"):
                raise AssertionError(f"MCP leaked session_id in login: {login}")

            public_session = await tool(session, "session")
            if contains_key(public_session, "session_id"):
                raise AssertionError(f"MCP leaked session_id in session: {public_session}")

            before = await tool(session, "game_state")
            if contains_key(before, "session_id") or "snapshot" in before:
                raise AssertionError(f"MCP state is not compact/safe: {before}")
            if before["P"]["x"] != 100.0:
                raise AssertionError(f"P must start at x=100: {before}")

            moved = await tool(session, "move", {"direction": "right"})
            if moved.get("sequence") != 1:
                raise AssertionError(f"first MCP sequence must be 1: {moved}")

            await asyncio.sleep(0.25)
            after = await tool(session, "game_state")
            if not after["P"]["x"] > before["P"]["x"]:
                raise AssertionError(
                    f"MCP did not move P right: {before['P']['x']} -> {after['P']['x']}"
                )

            stopped = await tool(session, "move", {"direction": "stop"})
            if stopped.get("sequence") != 2:
                raise AssertionError(f"MCP stop sequence must be 2: {stopped}")

            events = await tool(
                session,
                "recent_events",
                {"after_event_id": 1, "limit": 2},
            )
            page = events.get("events") or []
            if len(page) > 2:
                raise AssertionError(f"MCP event page exceeded limit: {events}")
            observed = [
                (event.get("client_id"), event.get("sequence"), event.get("move_x"))
                for event in page
                if event.get("kind") == "input"
            ]
            if observed != [("mcp", 1, 1), ("mcp", 2, 0)]:
                raise AssertionError(f"unexpected MCP events: {events}")
            if contains_key(events, "session_id"):
                raise AssertionError(f"MCP leaked session_id in events: {events}")

            logged_out = await tool(session, "logout")
            if not logged_out.get("logged_out"):
                raise AssertionError(f"bad logout: {logged_out}")
            if contains_key(logged_out, "session_id"):
                raise AssertionError(f"MCP leaked session_id in logout: {logged_out}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="game-v1-mcp-smoke-") as temp:
        temp_path = Path(temp)
        server_log_path = temp_path / "server.log"
        with server_log_path.open("w+", encoding="utf-8") as server_log:
            server = subprocess.Popen(
                [str(ROOT / "gameserver/v1/op/server.sh")],
                cwd=ROOT,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            try:
                wait_port(SERVER_PORT, server)

                asyncio.run(run_mcp_flow())

                if server.poll() is not None:
                    raise AssertionError("GameServer died during MCP smoke")

                with socket.create_connection(("127.0.0.1", HOST_PORT), timeout=0.5):
                    pass

                print("PASS game v1 MCP stdio smoke tools=9 auto_host=yes bounded=yes no_session_leak=yes")
                return 0
            except Exception:
                server_log.flush()
                print("----- GameServer log -----")
                print(server_log_path.read_text(encoding="utf-8", errors="replace"))
                raise
            finally:
                subprocess.run(
                    [str(ROOT / "gameclient/v1/op/host.sh"), "--stop"],
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                stop_group(server)


if __name__ == "__main__":
    raise SystemExit(main())
