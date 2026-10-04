"""Cross-version smoke test: openkg-webui's acp 0.12.1 client vs installed hermes-acp (acp 0.9.0).

initialize -> new_session -> one trivial prompt, printing every session/update received.
"""

import asyncio
import os
import sys
import time

import acp
import acp.schema as schema

HERMES_ACP = os.path.expanduser("~/.local/bin/hermes-acp")
CWD = "/tmp/hermes-acp-probe-cwd"
STDERR_LOG = "/tmp/hermes_acp_probe_stderr.log"

os.makedirs(CWD, exist_ok=True)
print("openkg client acp:", acp.PROTOCOL_VERSION, "| hermes-acp:", HERMES_ACP, flush=True)


class ProbeClient:
    def __init__(self):
        self.updates = []
        self.perm_calls = 0

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)
        t = type(update).__name__
        txt = ""
        for attr in ("content", "title", "status", "entries", "tool_call_id"):
            v = getattr(update, attr, None)
            if v is not None:
                txt = f"{attr}={str(v)[:160]}"
                break
        print(f"  [update] {t} {txt}", flush=True)

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.perm_calls += 1
        print(
            f"  [perm] {str(tool_call)[:200]} options={[getattr(o, 'option_id', '?') for o in options]}",
            flush=True,
        )
        # Pick the closest "allow once" option, else the first.
        pick = None
        for o in options:
            oid = str(getattr(o, "option_id", "") or "")
            if "allow" in oid:
                pick = oid
                break
        if pick is None and options:
            pick = str(getattr(options[0], "option_id", "") or "")
        outcome_cls = getattr(schema, "SelectedPermissionOutcome", None)
        if outcome_cls is not None and pick:
            return schema.RequestPermissionResponse(outcome=outcome_cls(option_id=pick))
        return schema.RequestPermissionResponse(outcome="cancelled")

    async def create_elicitation(self, message, mode, **kwargs):
        print(f"  [elicitation] {str(message)[:200]}", flush=True)
        decline = getattr(schema, "DeclineElicitationResponse", None)
        if decline is not None:
            return decline(action="decline")
        return None

    # Stubs the router may look for (only invoked if hermes sends the request).
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


async def main():
    err = open(STDERR_LOG, "w")
    cm = acp.spawn_stdio_transport(HERMES_ACP, env=dict(os.environ), cwd=CWD, stderr=err)
    reader, writer, process = await cm.__aenter__()
    client = ProbeClient()
    conn = acp.connect_to_agent(client, writer, reader, use_unstable_protocol=True)
    t0 = time.time()
    try:
        caps = schema.ClientCapabilities(
            elicitation=schema.ElicitationCapabilities(form=schema.ElicitationFormCapabilities()),
        )
        init = await asyncio.wait_for(
            conn.initialize(
                acp.PROTOCOL_VERSION,
                client_capabilities=caps,
                client_info=schema.Implementation(
                    name="openkg-webui", title="OPENKG-WebUI", version=""
                ),
            ),
            60,
        )
        print(
            f"[initialize ok {time.time() - t0:.1f}s] protocol={getattr(init, 'protocol_version', '?')} "
            f"agent={getattr(getattr(init, 'agent_info', None), 'name', '?')}",
            flush=True,
        )
        print("  capabilities:", getattr(init, "agent_capabilities", None), flush=True)
        print(
            "  auth_methods:",
            [getattr(m, "id", "?") for m in (getattr(init, "auth_methods", None) or [])],
            flush=True,
        )

        t1 = time.time()
        resp = await asyncio.wait_for(conn.new_session(cwd=CWD), 300)
        print(f"[new_session ok {time.time() - t1:.1f}s] raw={str(resp)[:500]}", flush=True)
        sid = str(resp.session_id)
        print("  config_options:", getattr(resp, "config_options", None), flush=True)
        print("  modes:", getattr(resp, "modes", None), flush=True)

        t2 = time.time()
        pr = await asyncio.wait_for(
            conn.prompt(session_id=sid, prompt=[acp.text_block("Reply with exactly: pong")]),
            360,
        )
        print(
            f"[prompt done {time.time() - t2:.1f}s] stop={getattr(pr, 'stop_reason', '?')} usage={getattr(pr, 'usage', None)}",
            flush=True,
        )
        print(
            f"[updates received: {len(client.updates)}] kinds="
            f"{sorted({type(u).__name__ for u in client.updates})}",
            flush=True,
        )
    except Exception as exc:
        print(f"[FAILED after {time.time() - t0:.1f}s] {type(exc).__name__}: {exc}", flush=True)
        raise
    finally:
        try:
            await cm.__aexit__(*sys.exc_info())
        except Exception:
            pass
        err.close()


asyncio.run(main())
