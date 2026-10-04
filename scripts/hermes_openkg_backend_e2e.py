"""E2E: openkg-webui's real AcpAgentLoopBackend against local hermes-acp.

Turn 1: plain reply. Turn 2 (same session/child): dangerous command -> approval
event -> respond_approval("once") -> turn completes.
"""

import asyncio
import os
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from openkg_webui.services.agent_loop.acp_backend import AcpAgentLoopBackend
from openkg_webui.services.agent_loop.protocol import AgentLoopRequest

CWD = "/tmp/hermes-acp-probe-cwd"
os.makedirs(CWD, exist_ok=True)
# The approval scenario needs a command Hermes' classifier treats as
# dangerous (a $HOME-adjacent recursive delete — /tmp deletes are not
# gated). The target is created by this script alone and carries the
# probe marker in its name; nothing pre-existing is ever touched.
PROBE_DIR = os.path.expanduser("~/hermes-openkg-approval-probe")
os.makedirs(PROBE_DIR, exist_ok=True)


async def main():
    backend = AcpAgentLoopBackend(
        name="hermes-probe",
        command=os.path.expanduser("~/.local/bin/hermes-acp"),
        base_args=[],
        env={},
        timeout_seconds=180,
    )

    async def one_turn(prompt, session_id, label):
        print(f"\n=== {label} ===", flush=True)
        kinds = []
        approval_seen = 0
        text_parts = []
        req = AgentLoopRequest(prompt=prompt, session_id=session_id, workdir=CWD)
        async for event in backend.run(req):
            kinds.append(event.kind)
            brief = (event.text or "").replace("\n", " ")[:110]
            print(f"  [{event.kind}] name={event.name!r} {brief}", flush=True)
            if event.kind == "content":
                text_parts.append(event.text)
            if event.kind == "approval_request":
                approval_seen += 1
                rid = event.data.get("request_id") or ""
                print(f"  -> respond_approval({rid!r}, 'once')", flush=True)
                await backend.respond_approval(rid, "once")
            if event.kind == "error":
                print(f"  !! error: {event.text[:300]}", flush=True)
        print(f"  kinds={kinds}", flush=True)
        return "".join(text_parts), approval_seen

    text1, appr1 = await one_turn("Reply with exactly: pong-openkg", "probe-openkg-1", "turn 1")
    print("turn1 text:", text1[-120:], "| approvals:", appr1, flush=True)

    text2, appr2 = await one_turn(
        "Use your terminal tool to run exactly this command: `rm -rf ~/hermes-openkg-approval-probe` (a directory this probe script just created). "
        "Then say in one short sentence what happened.",
        "probe-openkg-1",
        "turn 2 (same session, approval expected)",
    )
    print("turn2 text:", text2[-200:], "| approvals:", appr2, flush=True)

    # Teardown the child so the script exits cleanly.
    mgr = backend._manager
    for key in list(mgr._handles):
        await mgr.discard(key)


asyncio.run(main())
