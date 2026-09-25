"""Operator gate: prove the OpenCode-facing GameLab MCP is ready for Yuki Spine work."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[2]


async def main_async() -> None:
    print("CHECK Yuki GameLab MCP startup", flush=True)
    params = StdioServerParameters(
        command=str(ROOT / "gamelab/op/gamelab.sh"),
        args=["serve"],
        cwd=str(ROOT),
        env={**os.environ},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=10.0)
            print("CHECK Yuki GameLab model_info", flush=True)
            response = await asyncio.wait_for(
                session.call_tool("model_info", {}),
                timeout=10.0,
            )
            if response.is_error:
                raise RuntimeError(f"model_info MCP error: {response.content}")
            payload = response.structured_content
            if not isinstance(payload, dict):
                # MCP 2.2 fallback for result payloads without structuredContent.
                import json
                chunks = [
                    item.text for item in response.content
                    if getattr(item, "type", None) == "text"
                ]
                payload = json.loads("\n".join(chunks))

            motors = payload.get("motors") or []
            certified = [
                item for item in motors
                if item.get("status") == "certified"
                and item.get("certified") is True
                and item.get("brain_ready") is True
            ]
            selected_motor_id = payload.get("selected_motor_id")
            preflight_error = payload.get("training_preflight_error")
            if (
                not certified
                or payload.get("trainable") is not True
                or not isinstance(selected_motor_id, str)
                or not selected_motor_id
                or preflight_error is not None
            ):
                raise RuntimeError(
                    "GameLab is not ready for Yuki fresh Spine training. "
                    "The default 'best' Motor must resolve and load into a fresh Spine policy. "
                    f"model_info={payload}"
                )
            print(
                "PASS Yuki GameLab readiness "
                f"certified_motors={len(certified)} "
                f"selected_motor={selected_motor_id} "
                f"checkpoint_ready={bool(payload.get('checkpoint_ready'))}"
            )


def main() -> int:
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
