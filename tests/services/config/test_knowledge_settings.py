"""``knowledge`` system-settings block（docs/knowledge-center-port-design.md §三；
Phase 1a T2）：归一化 round-trip 与默认值。"""

from __future__ import annotations

from pathlib import Path

from openkg_webui.services.config.runtime_settings import RuntimeSettingsService


def _normalize_knowledge(raw: dict, settings_dir: Path) -> dict:
    service = RuntimeSettingsService(settings_dir, process_env={})
    return service._normalize_system({"knowledge": raw})["knowledge"]


def test_defaults(tmp_path: Path) -> None:
    assert _normalize_knowledge({}, tmp_path) == {
        "version": 1,
        "enabled": False,
        "base_url": "",
        "api_key": "",
    }


def test_roundtrip_and_coercion(tmp_path: Path) -> None:
    block = _normalize_knowledge(
        {"enabled": "yes", "base_url": "http://127.0.0.1:9380/", "api_key": "sk-x", "junk": 1},
        tmp_path,
    )
    assert block == {
        "version": 1,
        "enabled": True,
        "base_url": "http://127.0.0.1:9380",
        "api_key": "sk-x",
    }
