"""Local agent-loop detection — DeepMentor's probe pattern, OPENKG-WebUI-scoped.

Two probe families, matching the backend families:

* **CLI** — a ``shutil.which`` PATH probe (Windows PATHEXT-safe, so npm
  ``.cmd``/``.bat`` shims are found), falling back to a transport's
  ``command_paths`` (well-known absolute install locations, stat-only).
  Fast and side-effect free, which is why it runs on settings-page load
  rather than behind a button; the definitive command *execution* check
  stays on the explicit Test action.
* **HTTP** — a short-timeout reachability GET against a configured service
  URL. Any HTTP response (even 404) proves the service is up; the consult
  contract has no health endpoint, and POSTing the real turn endpoint would
  run a real agent turn. Local URLs (loopback hosts) are labeled ``local``
  so the UI can mark "本地 / 远端" and the auto-primary rule can prefer
  local Intellect deployments.

Preset-level CLI probes answer "is this agent CLI installed on this
machine" for the settings preset picker; profile-level probes answer "is
*my* configured loop reachable" for each saved profile card. The structured
``path``/``via_fallback`` fields let the UI prefill a profile's command with
a fallback hit without parsing the human ``detail`` string.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import os
import shutil
from typing import Any
from urllib.parse import urlsplit

import httpx

#: Reachability probe budget — short enough not to stall a settings-page
#: load, long enough for a waking local service.
_HTTP_PROBE_TIMEOUT_SECONDS = 2.5

_LOCAL_HOSTNAMES = frozenset({"localhost", "::1", "0.0.0.0"})


@dataclass
class DetectResult:
    """One probe outcome; ``key`` is a preset name or a profile id.

    ``path`` is the executable actually resolved (PATH hit or fallback hit);
    ``via_fallback`` marks a ``command_paths`` hit so the UI can prefill the
    absolute command without parsing ``detail``.
    """

    key: str
    label: str
    family: str  # "cli" | "http"
    local: bool
    available: bool
    detail: str = ""
    path: str = ""
    via_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_local_url(url: str) -> bool:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return host in _LOCAL_HOSTNAMES or host.startswith("127.")


def _expand_candidate(raw: str) -> str:
    """Expand one ``command_paths`` entry; ``""`` when it cannot be concrete.

    ``~`` always expands. ``$HERMES_HOME`` falls back to ``~/.hermes`` when
    the variable is unset (the documented default home). Any variable that
    stays unresolved after :func:`os.path.expandvars` disqualifies the
    candidate — a literal ``$VAR`` in a path is never a hit.
    """
    path = os.path.expanduser(str(raw or "").strip())
    if "$HERMES_HOME" in path and not os.environ.get("HERMES_HOME"):
        path = path.replace("$HERMES_HOME", os.path.expanduser("~/.hermes"))
    path = os.path.expandvars(path)
    return "" if "$" in path else path


def resolve_cli_command(command: str, command_paths: tuple[str, ...] = ()) -> tuple[str, bool]:
    """``(path, via_fallback)`` for one CLI command, or ``("", False)``.

    PATH first (``shutil.which`` semantics, executability included), then the
    transport's well-known locations in order — stat-only, no subprocess.
    """
    command = str(command or "").strip()
    if not command:
        return "", False
    if os.path.sep in command or (os.altsep and os.altsep in command):
        return (command, False) if os.access(command, os.X_OK) else ("", False)
    resolved = shutil.which(command)
    if resolved:
        return resolved, False
    for raw in command_paths or ():
        candidate = _expand_candidate(raw)
        if candidate and os.access(candidate, os.X_OK):
            return candidate, True
    return "", False


def detect_cli(
    key: str,
    label: str,
    command: str,
    command_paths: tuple[str, ...] = (),
) -> DetectResult:
    """PATH-probe one CLI command (no subprocess, no version call), falling
    back to the transport's well-known install locations."""
    command = str(command or "").strip()
    if not command:
        return DetectResult(key, label, "cli", True, False, "no command configured")
    resolved, via_fallback = resolve_cli_command(command, command_paths)
    if resolved:
        detail = resolved
    elif (os.path.sep in command or (os.altsep and os.altsep in command)) and os.path.exists(
        command
    ):
        # An absolute path that is there but cannot run: "not found" would
        # send the operator chasing the wrong fix.
        detail = f"'{command}' exists but is not executable"
    else:
        detail = f"'{command}' not found on PATH"
    return DetectResult(
        key,
        label,
        "cli",
        True,
        bool(resolved),
        detail,
        path=resolved,
        via_fallback=via_fallback,
    )


async def detect_http(key: str, label: str, url: str) -> DetectResult:
    """Reachability-probe one service URL. TLS errors still count as
    reachable — the probe only asks "is something answering", never
    transfers agent data."""
    url = str(url or "").strip()
    local = is_local_url(url)
    if not url:
        return DetectResult(key, label, "http", local, False, "no URL configured")
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_PROBE_TIMEOUT_SECONDS,
            verify=False,  # probe only — no credentials, no body
            follow_redirects=True,
            # Direct connection: a system proxy answering on the machine
            # would turn "unreachable" into a proxy error page.
            trust_env=False,
        ) as client:
            response = await client.get(url)
        return DetectResult(key, label, "http", local, True, f"HTTP {response.status_code}")
    except Exception as exc:  # noqa: BLE001 — any probe failure is a result
        return DetectResult(key, label, "http", local, False, str(exc)[:200])


async def detect_agent_loops(block: dict[str, Any] | None = None) -> list[DetectResult]:
    """Probe every preset CLI plus every configured profile.

    Returns preset-level CLI results (keyed by preset name, for the picker)
    followed by profile-level results (keyed by profile id, for the cards).
    """
    from .builtin import PRESETS, preset_transports, resolve_transport, transport_key
    from .settings import get_agent_loop_settings

    settings = block if block is not None else get_agent_loop_settings()
    profiles = [item for item in (settings.get("profiles") or []) if isinstance(item, dict)]

    cli_results: list[DetectResult] = []
    preset_http_coros = []
    profile_coros = []

    for preset in PRESETS.values():
        # Each transport of a preset is probed on its own terms and keyed as
        # the picker's buttons are (a preset offering several shows one badge
        # per button, not one for whichever transport happened to be first).
        for transport in preset_transports(preset.name):
            key = transport_key(preset.name, transport.id)
            label = preset.name if not transport.id else f"{preset.name}:{transport.id}"
            if transport.family == "cli" and transport.command:
                cli_results.append(
                    detect_cli(key, label, transport.command, transport.command_paths)
                )
            elif transport.family == "http" and transport.probe_url:
                # Preset-level reachability probe for HTTP backends that ship a
                # well-known local default (e.g. the Intellect api_server health
                # endpoint) — remote services still need an explicit profile.
                preset_http_coros.append(detect_http(key, label, transport.probe_url))

    for profile in profiles:
        preset = PRESETS.get(str(profile.get("preset") or ""))
        label = str(profile.get("name") or profile.get("preset") or "profile")
        if preset is not None:
            # Probe what this profile actually builds: its own transport may be
            # the CLI child or the HTTP service, whichever the preset offers.
            transport = resolve_transport(preset.name, str(profile.get("transport") or ""))
            if transport is not None and transport.family == "cli":
                command = str(profile.get("command") or "").strip()
                if command:
                    # An explicit command is authoritative: probe it bare.
                    profile_coros.append(
                        asyncio.to_thread(detect_cli, str(profile.get("id")), label, command)
                    )
                elif transport.command:
                    # Defaulted command: carry the transport's fallback
                    # locations so a non-PATH install still lights the card.
                    profile_coros.append(
                        asyncio.to_thread(
                            detect_cli,
                            str(profile.get("id")),
                            label,
                            transport.command,
                            transport.command_paths,
                        )
                    )
            elif transport is not None and str(profile.get("url") or "").strip():
                profile_coros.append(
                    detect_http(str(profile.get("id")), label, str(profile.get("url")))
                )
        elif str(profile.get("url") or "").strip():
            profile_coros.append(
                detect_http(str(profile.get("id")), label, str(profile.get("url")))
            )

    probed = await asyncio.gather(*preset_http_coros, *profile_coros)
    return [*cli_results, *probed]


__all__ = [
    "DetectResult",
    "detect_agent_loops",
    "detect_cli",
    "detect_http",
    "is_local_url",
    "resolve_cli_command",
]
