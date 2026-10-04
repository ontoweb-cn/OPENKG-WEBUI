"""E2E: openkg-webui's runs-protocol HTTP backend against hermes gateway api_server.

The proposed hermes 'http' transport is (family=http, turn_path=/v1/runs,
protocol=runs) — identical code path to the intellect-team preset, so we drive
that preset with hermes's URL to validate the wire contract end to end.
"""

import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from openkg_webui.services.agent_loop import build_agent_loop_backend
from openkg_webui.services.agent_loop.protocol import AgentLoopRequest

URL = "http://127.0.0.1:8642"
KEY = "openkg-probe-key-0123456789"


async def main():
    backend = build_agent_loop_backend(
        {
            "preset": "intellect-team",
            "url": URL,
            "api_key": KEY,
            "timeout_seconds": 180,
        }
    )
    print(
        "backend:",
        type(backend).__name__,
        "turn_path:",
        getattr(backend, "turn_path", None),
        flush=True,
    )

    kinds = []
    async for event in backend.run(
        AgentLoopRequest(
            prompt="Reply with exactly: pong-runs",
            session_id="probe-runs-1",
            workdir="/tmp/hermes-acp-probe-cwd",
        )
    ):
        kinds.append(event.kind)
        brief = (event.text or "").replace("\n", " ")[:130]
        print(f"  [{event.kind}] name={event.name!r} {brief}", flush=True)
    print("kinds:", kinds, flush=True)


asyncio.run(main())
