# OPENKG-WebUI

OPENKG-WebUI is an agent-native Web UI framework, for OPENKG KAG and ONTOWEB Intellect Agent.

This fork strips the product capability layer down to a **framework shell** and
prepares the codebase for a custom KAG backend integration:

- **Kept** — the LLM provider layer (OpenAI Chat Completions + Responses API
  dual-protocol, Anthropic, Azure, Codex, Copilot, ...), the turn orchestrator
  with capability/tool registries, the streaming event bus, SQLite + PocketBase
  session storage, runtime settings, multi-user/auth/grants, Partners (IM
  channels), the Next.js web front end, and CLI/SDK entry points.
- **Next** — the default `chat` capability is a stub that completes every turn
  with a localized shell notice; the KAG backend plugs in at
  `openkg_webui/runtime/orchestrator.py` -> `openkg_webui/capabilities/chat/`.

## Install (development)

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"   # Windows
# or: python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"
cd web && npm ci
```

## Run

`openkg-webui` is installed into the virtual environment, so activate it first
(or call `.venv/bin/openkg-webui` directly):

```bash
source .venv/bin/activate    # Windows: .venv\Scripts\activate

openkg-webui start                 # backend + frontend together
openkg-webui serve --port 8082     # API server only
openkg-webui run chat "hello"      # single turn through the stub capability
```

`openkg-webui start` runs in the foreground until Ctrl+C. For a background instance,
start detached and manage it with `restart` / `stop`:

```bash
openkg-webui start --detach        # background launcher; log: data/user/runtime/launcher.log
openkg-webui restart               # stop the running launcher, then start it again
openkg-webui stop                  # stop the background launcher
```

`openkg-webui restart` keeps the frontend mode the running launcher was started
with; pass `--dev` / `--prod` to switch it. If a port is already taken,
`openkg-webui start` lists the occupying processes and offers to change ports or
stop them (interactive terminals only).

## Provider auth

Provider auth (`openai-codex` OAuth login; `github-copilot` validates an existing Copilot auth session; `codebuddy` validates CodeBuddy SDK auth and starts login when needed) is managed through `openkg-webui provider login <provider>`. For local Codex OAuth bridging in containers, see `CONTAINERIZATION.md#temporary-local-codex-oauth-bridge`.

## Deployment

The web app stays deployable under a subpath
(e.g. `https://your.host/openkg-webui` behind Kubernetes/Ingress). See
`CONTAINERIZATION.md`, `deploy/k8s/`, and the subpath rules in `AGENTS.md`.

## Changelog

Release notes live in [`CHANGELOG.md`](CHANGELOG.md). The current version is the
single source of truth in `openkg_webui/__version__.py`.

## License

Apache-2.0, inherited from the upstream project; see `LICENSE` and
`THIRD_PARTY_NOTICES.md`.
