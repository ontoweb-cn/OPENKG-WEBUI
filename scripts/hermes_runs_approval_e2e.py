"""E2E: approval round-trip through the runs transport (openkg RunsAgentLoopBackend <-> hermes gateway).

Ask for a recursive delete of a home-dir path -> hermes should raise approval.request
-> openkg surfaces approval_request -> we answer "once" via respond_approval -> turn completes.
"""

import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from openkg_webui.services.agent_loop import build_agent_loop_backend
from openkg_webui.services.agent_loop.protocol import AgentLoopRequest

URL = "http://127.0.0.1:8642"
# The rm -rf target below is created (empty) by this script before the turn;
# $HOME-adjacent deletes are what Hermes' approval classifier gates.
import os as _os

_os.makedirs(_os.path.expanduser("~/hermes-runs-approval-probe"), exist_ok=True)
KEY = "openkg-probe-key-0123456789"


async def main():
    backend = build_agent_loop_backend(
        {"preset": "intellect-team", "url": URL, "api_key": KEY, "timeout_seconds": 240}
    )
    approvals = 0
    kinds = []
    async for event in backend.run(
        AgentLoopRequest(
            prompt=(
                "Use your terminal tool to run exactly this command: "
                "`rm -rf ~/hermes-runs-approval-probe`. "
                "Then answer in one short sentence what happened."
            ),
            session_id="probe-runs-approval-1",
            workdir="/tmp/hermes-acp-probe-cwd",
        )
    ):
        kinds.append(event.kind)
        brief = (event.text or "").replace("\n", " ")[:120]
        print(f"  [{event.kind}] name={event.name!r} {brief}", flush=True)
        if event.kind == "approval_request":
            approvals += 1
            rid = event.data.get("request_id") or ""
            print(f"  -> respond_approval({rid!r}, 'once')", flush=True)
            await backend.respond_approval(rid, "once")
    print("kinds:", kinds, flush=True)
    print("approvals:", approvals, flush=True)


asyncio.run(main())
