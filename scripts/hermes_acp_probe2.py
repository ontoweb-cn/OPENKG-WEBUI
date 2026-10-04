"""Probe 2: tool use + approval round-trip + cross-process session resume (load_session).

Turn 1: forces a terminal tool call (approval may fire -> we allow).
Then the child is killed. A NEW child re-attaches the same session via session/load,
and Turn 2 asks the agent what it did before — verifying state.db persistence + replay.
"""

import asyncio
import os
import sys
import time

import acp
import acp.schema as schema

HERMES_ACP = os.path.expanduser("~/.local/bin/hermes-acp")
CWD = "/tmp/hermes-acp-probe-cwd"
os.makedirs(CWD, exist_ok=True)


class Client:
    def __init__(self, conn_holder):
        self.updates = []
        self.conn_holder = conn_holder
        self.pending_perm = []

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)
        t = type(update).__name__
        brief = ""
        if t == "AgentMessageChunk":
            brief = str(getattr(update.content, "text", ""))[:80]
        elif t in ("ToolCallStart", "ToolCallUpdate", "ToolCallProgress"):
            brief = f"title={str(getattr(update, 'title', ''))[:100]} status={getattr(update, 'status', None)} kind={getattr(update, 'kind', None)}"
        elif t == "AgentThoughtChunk":
            brief = str(getattr(update.content, "text", ""))[:60]
        elif t == "AgentPlanUpdate":
            brief = "plan"
        print(f"  [{t}] {brief}", flush=True)

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        print(
            f"  [PERMISSION REQUEST] title={str(getattr(tool_call, 'title', ''))[:120]!r} "
            f"options={[(getattr(o, 'option_id', ''), getattr(o, 'kind', '')) for o in options]}",
            flush=True,
        )
        for o in options:
            if str(getattr(o, "option_id", "")) == "allow_once":
                print("  -> answering allow_once", flush=True)
                return {"outcome": {"outcome": "selected", "optionId": "allow_once"}}
        return {"outcome": {"outcome": "cancelled"}}

    async def create_elicitation(self, message, mode, **kwargs):
        print(f"  [ELICITATION] {str(message)[:120]}", flush=True)
        return (
            schema.DeclineElicitationResponse(action="decline")
            if hasattr(schema, "DeclineElicitationResponse")
            else None
        )

    async def read_text_file(self, *a, **k):
        raise NotImplementedError

    async def write_text_file(self, *a, **k):
        raise NotImplementedError

    async def create_terminal(self, *a, **k):
        raise NotImplementedError

    async def terminal_output(self, *a, **k):
        raise NotImplementedError

    async def wait_for_terminal_exit(self, *a, **k):
        raise NotImplementedError

    async def kill_terminal(self, *a, **k):
        raise NotImplementedError

    async def release_terminal(self, *a, **k):
        raise NotImplementedError

    async def complete_elicitation(self, *a, **k):
        return None

    async def ext_method(self, method, params):
        return {}

    async def ext_notification(self, method, params):
        return None


async def spawn(probe_name):
    err = open(f"/tmp/hermes_acp_probe2_{probe_name}_stderr.log", "w")
    env = dict(os.environ)
    # Keep the probe from inheriting any interactive-ness knobs the shell may carry.
    cm = acp.spawn_stdio_transport(HERMES_ACP, env=env, cwd=CWD, stderr=err)
    reader, writer, process = await cm.__aenter__()
    holder = {}
    client = Client(holder)
    conn = acp.connect_to_agent(client, writer, reader, use_unstable_protocol=True)
    init = await asyncio.wait_for(
        conn.initialize(
            acp.PROTOCOL_VERSION,
            client_capabilities=schema.ClientCapabilities(),
            client_info=schema.Implementation(
                name="openkg-webui", title="OPENKG-WebUI", version=""
            ),
        ),
        60,
    )
    print(
        f"spawned {probe_name}: agent={getattr(init.agent_info, 'name', '?')} caps={getattr(init.agent_capabilities,'load_session',None)=}",
        flush=True,
    )
    return cm, conn, client, err


async def main():
    # ---- Turn 1 on child A: tool call (approval likely) + note a fact.
    cm, conn, client, err = await spawn("a")
    try:
        resp = await asyncio.wait_for(conn.new_session(cwd=CWD), 300)
        sid = str(resp.session_id)
        print(f"session_id={sid}", flush=True)
        t = time.time()
        pr = await asyncio.wait_for(
            conn.prompt(
                session_id=sid,
                prompt=[
                    acp.text_block(
                        "Use your terminal tool to run this exact command: "
                        "`rm -rf /tmp/hermes-acp-probe-cwd/final-answer.txt && echo probe-marker-42 > /tmp/hermes-acp-probe-cwd/final-answer.txt`. "
                        "Then tell me, in one short sentence, what the file /tmp/hermes-acp-probe-cwd/final-answer.txt now contains."
                    )
                ],
            ),
            420,
        )
        print(
            f"turn1 done in {time.time() - t:.1f}s stop={getattr(pr, 'stop_reason', '?')}",
            flush=True,
        )
        kinds = [type(u).__name__ for u in client.updates]
        print(f"turn1 update kinds: {kinds}", flush=True)
    finally:
        try:
            await cm.__aexit__(*sys.exc_info())
        except Exception:
            pass
        err.close()
    await asyncio.sleep(1.0)

    # ---- Child B: re-attach the same session via load_session, then ask for recall.
    cm2, conn2, client2, err2 = await spawn("b")
    try:
        try:
            lr = await asyncio.wait_for(conn2.load_session(cwd=CWD, session_id=sid), 300)
            print(f"load_session ok: {str(lr)[:200]}", flush=True)
        except Exception as exc:
            print(f"load_session FAILED: {type(exc).__name__}: {exc}", flush=True)
            raise
        # The replay arrives as session/update before the response; count what we saw.
        print(
            f"replayed updates during load: {[type(u).__name__ for u in client2.updates]}",
            flush=True,
        )
        t = time.time()
        pr2 = await asyncio.wait_for(
            conn2.prompt(
                session_id=sid,
                prompt=[
                    acp.text_block(
                        "What did you write into final-answer.txt earlier? Answer in one short sentence."
                    )
                ],
            ),
            420,
        )
        print(
            f"turn2 done in {time.time() - t:.1f}s stop={getattr(pr2, 'stop_reason', '?')}",
            flush=True,
        )
        texts = []
        for u in client2.updates:
            if type(u).__name__ == "AgentMessageChunk":
                c = getattr(u, "content", None)
                txt = getattr(c, "text", "")
                if txt:
                    texts.append(str(txt))
        print("turn2 assistant text:", "".join(texts)[-400:], flush=True)
    finally:
        try:
            await cm2.__aexit__(*sys.exc_info())
        except Exception:
            pass
        err2.close()


asyncio.run(main())
