# OPENKG-WebUI — Agent-Native Architecture

## Overview

OPENKG-WebUI is an **agent-native** framework organized around multi-stage
**Capabilities** that take over a turn, exposed through three entry points:
CLI, WebSocket API, and Python SDK.

This fork ships a **framework shell**: the runtime, provider, storage, and web
layers are complete. The conversation backend is an external **agent loop** —
not a plain LLM call — selected at runtime via the `agent_loop` settings block
(`openkg_webui/services/agent_loop/`): CLI backends (Claude Code, Codex, OpenCode,
custom) and HTTP service backends (Intellect community/team, Hermes,
AgentScope, custom). While no backend is configured, `chat` is a stub that
completes every turn with a localized notice. See `ARCHITECTURE.md` for the
wire contracts and trust boundaries.

## Architecture

```
Entry Points:  CLI (Typer)  |  WebSocket /ws  |  Python SDK
                    ↓                   ↓                   ↓
              ┌─────────────────────────────────────────────────┐
              │                ChatOrchestrator                 │
              │   routes UnifiedContext -> selected Capability  │
              │   (defaults to `chat`)                          │
              └──────────┬──────────────┬───────────────────────┘
                         │              │
              ┌─────────▼─────────┐
              │ CapabilityRegistry │
              │   (Level 2)        │
              └────────┬──────────┘
                       │ chat delegates to
            ┌──────────▼──────────┐
            │  AgentLoopBackend   │
            │  CLI subprocess | HTTP │
            └─────────────────────┘
```

All capabilities emit on a shared `StreamBus`; the orchestrator fans events out
to consumers. Runtime settings live in `data/user/settings/*.json` —
project-root `.env` files are intentionally ignored.

### Tools — none

There is no tool layer: built-in prompt-time tools and the MCP client stack
(`services/mcp/`, its routers, and the `mcp_tools` grant dimension) are both
removed. The agent-loop backend carries its own tooling.

### Level 2 — Capabilities

`chat` is the only built-in capability (`openkg_webui/capabilities/chat/`): with an
agent-loop backend configured it delegates the turn and maps the backend's
neutral events onto the `StreamBus`; otherwise it is the shell stub. All
capabilities converge on `emit_capability_result()` in
`openkg_webui/capabilities/_shared.py` so every turn emits the same envelope
(response payload + `cost_summary` from `UsageTracker`).

## CLI Usage

```bash
# Install
pip install openkg-webui      # Full app (CLI + Web/API + packaged Web assets)
pip install openkg-webui-cli  # CLI-only

# Run the chat capability (stub notice without an agent_loop backend)
openkg-webui run chat "Explain Fourier transform"

# Interactive REPL
openkg-webui chat

# Server
openkg-webui serve --port 8082       # API server only
openkg-webui start                   # backend + frontend together
```

## Key Files

| Path                                       | Purpose                              |
| ------------------------------------------ | ------------------------------------ |
| `openkg_webui/runtime/orchestrator.py`           | `ChatOrchestrator` — unified entry   |
| `openkg_webui/runtime/launcher.py`               | Backend + frontend lifecycle / port discovery |
| `openkg_webui/runtime/registry/`                 | Capability registry                  |
| `openkg_webui/runtime/bootstrap/builtin_capabilities.py` | Built-in capability class paths |
| `openkg_webui/services/config/runtime_settings.py` | JSON settings + process-env overrides |
| `openkg_webui/core/stream.py`, `stream_bus.py`   | StreamEvent protocol + async fan-out |
| `openkg_webui/core/capability_protocol.py`       | `TurnCapability` + `CapabilityManifest` |
| `openkg_webui/core/context.py`                   | `UnifiedContext` dataclass           |
| `openkg_webui/capabilities/`                     | Built-in capability implementations  |
| `openkg_webui/services/agent_loop/`              | Agent-loop backends (CLI/HTTP) + presets |
| `openkg_webui/app.py`                            | `OPENKGWebUIApp` — Python SDK facade      |
| `openkg_webui_cli/main.py`                       | Typer CLI entry point                |
| `openkg_webui/api/routers/unified_ws.py`         | Unified WebSocket endpoint           |

## LLM Providers

`openkg_webui/services/llm/provider_core/` keeps the full provider stack. Endpoints
speak either wire protocol — OpenAI **Chat Completions**
(`/v1/chat/completions`) or the newer **Responses API** (`/v1/responses`) —
selected per provider via `WireAPI` (`auto` by default: Chat Completions
everywhere, Responses for OpenAI reasoning models, with a circuit breaker and
fallback).

## Dependency Layers

Public install paths and source extras are defined in `pyproject.toml`.

```
pip install openkg-webui      — Full app (CLI + Web/API + packaged Web assets)
pip install openkg-webui-cli  — CLI-only (LLM + providers + document parsing)
pip install -e .        — Source install for development

Source extras (.[extra]):
.[cli]            — CLI-only dependency set
.[server]         — Web/API server dependencies
.[codebuddy]      — CodeBuddy agent SDK
.[video-learning]  — YouTube transcript ingestion
.[math-animator]  — Manim animations
.[parse-*]        — Alternative document parsing engines
.[dev]            — Test / lint tooling
.[all]            — Everything above
```

## Web Rule: Subpath Deployment

The web app must stay deployable under a **subpath** (e.g.
`https://your.host/openkg-webui` behind Kubernetes/Ingress), not only at a domain
root. When writing code under `web/`, never assume the app is served from `/`:

- API / WS / SSE URLs — build them via `apiUrl()` / `wsUrl()`
  (`web/shared/api/client.ts`) so a basePath can be applied centrally. Do not
  call `fetch` / `new WebSocket` / `new EventSource` with raw `"/api/…"` /
  `"/ws/…"` strings.
- Auth redirects — go through `loginHref()` / `normalizeInternalReturnPath()`
  (`web/shared/auth/return-url.ts`) and the path constants in
  `web/lib/proxy-policy.ts`; keep `proxy.ts` and `isBackendPath()` prefix-aware.
- Raw HTML asset references (e.g. `<svg><image href="/logo.png">`) bypass
  Next's basePath handling — use `next/image` or a helper instead.
- `Link` / `router.push` destinations are app-relative paths and are
  basePath-prefixed by the framework — never prepend the domain or a
  deployment prefix by hand.
