"""CLI command resolution: PATH first, then a transport's well-known paths."""

from __future__ import annotations

from pathlib import Path

from openkg_webui.services.agent_loop.detect import detect_cli, resolve_cli_command


def test_path_hit_is_not_marked_as_fallback(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "tool"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    resolved, via_fallback = resolve_cli_command("tool", ("~/nowhere/tool",))
    assert resolved == str(exe)
    assert via_fallback is False

    result = detect_cli("k", "K", "tool", ("~/nowhere/tool",))
    assert result.available is True
    assert result.via_fallback is False
    assert result.path == str(exe)


def test_fallback_hit_resolves_and_is_marked(tmp_path: Path, monkeypatch) -> None:
    # A machine may genuinely have the command on PATH (this one does); the
    # fallback path only fires when PATH misses, so scrub it.
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    install = tmp_path / "install" / "bin"
    install.mkdir(parents=True)
    exe = install / "hermes-acp"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)

    resolved, via_fallback = resolve_cli_command("hermes-acp", (str(install / "hermes-acp"),))
    assert resolved == str(install / "hermes-acp")
    assert via_fallback is True

    result = detect_cli("hermes:acp", "H", "hermes-acp", (str(install / "hermes-acp"),))
    assert result.available is True
    assert result.via_fallback is True
    assert result.path == str(install / "hermes-acp")


def test_unresolved_variable_never_matches(tmp_path: Path, monkeypatch) -> None:
    """A literal ``$VAR`` left after expansion is not a filesystem hit."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    resolved, via_fallback = resolve_cli_command("hermes-acp", ("$NO_SUCH_VAR_AT_ALL/hermes-acp",))
    assert resolved == ""
    assert via_fallback is False


def test_hermes_home_defaults_to_the_documented_home(tmp_path: Path, monkeypatch) -> None:
    """``$HERMES_HOME`` with the variable unset resolves to ``~/.hermes``."""
    monkeypatch.delenv("HERMES_HOME", raising=False)
    from openkg_webui.services.agent_loop.detect import _expand_candidate

    expanded = _expand_candidate("$HERMES_HOME/hermes-agent/venv/bin/hermes-acp")
    assert expanded.startswith(str(Path.home() / ".hermes"))

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    expanded = _expand_candidate("$HERMES_HOME/hermes-agent/venv/bin/hermes-acp")
    assert expanded.startswith(str(tmp_path))


def test_non_executable_file_is_not_a_hit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    exe = tmp_path / "hermes-acp"
    exe.write_text("data")
    exe.chmod(0o644)  # not executable — shutil.which semantics

    resolved, via_fallback = resolve_cli_command("hermes-acp", (str(exe),))
    assert resolved == ""
    assert via_fallback is False


def test_absolute_command_skips_the_fallback_list(tmp_path: Path) -> None:
    """An absolute command is authoritative: it resolves on its own terms."""
    exe = tmp_path / "sh-like"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    resolved, via_fallback = resolve_cli_command(str(exe), ("~/nowhere",))
    assert resolved == str(exe)
    assert via_fallback is False
