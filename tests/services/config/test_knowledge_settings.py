"""``knowledge`` system-settings block（docs/knowledge-center-port-design.md §三；
Phase 1a T2）：归一化 round-trip 与默认值。"""

from __future__ import annotations

from pathlib import Path

from openkg_webui.services.config.runtime_settings import RuntimeSettingsService


def _normalize_knowledge(raw: dict) -> dict:
    service = RuntimeSettingsService(Path("/nonexistent"), process_env={})
    return service._normalize_system({"knowledge": raw})["knowledge"]


def test_defaults() -> None:
    assert _normalize_knowledge({}) == {
        "version": 1,
        "enabled": False,
        "base_url": "",
        "api_key": "",
    }


def test_roundtrip_and_coercion() -> None:
    block = _normalize_knowledge(
        {"enabled": "yes", "base_url": "http://127.0.0.1:9380/", "api_key": "sk-x", "junk": 1}
    )
    assert block == {
        "version": 1,
        "enabled": True,
        "base_url": "http://127.0.0.1:9380",
        "api_key": "sk-x",
    }
