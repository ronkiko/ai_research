"""Exact cold-start smoke for the GameLab stdio MCP used by OpenCode."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[2]


async def main_async() -> None:
    params = StdioServerParameters(
        command=str(ROOT / "gamelab/op/gamelab.sh"),
        args=["serve"],
        cwd=str(ROOT),
        env={**os.environ},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30.0)
            tools = await asyncio.wait_for(session.list_tools(), timeout=30.0)
            names = {tool.name for tool in tools.tools}
            required = {"health", "model_info", "training_start", "verify_start", "run_start"}
            missing = sorted(required - names)
            if missing:
                raise RuntimeError(f"GameLab MCP started but tools are missing: {missing}")
            print(f"PASS gamelab MCP startup tools={len(names)}")


def main() -> int:
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
