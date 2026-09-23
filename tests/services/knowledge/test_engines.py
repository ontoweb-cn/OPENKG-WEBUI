"""知识引擎抽象（Phase 3 T1/T2）回归测试。

覆盖评审确认的设计点：
- 注册表解析：pin 命中 → 该引擎；未命中 → 默认引擎（P2-2）；
- 能力集合为常量取值（P3，UI 可 switch）；
- provider 协议符合性（request/upload/chat_binding/mcp_binding，P1-1）；
- chat_binding 的三态（未启用 None / 无勾选 scope / 有勾选 kb_ids）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openkg_webui.services.knowledge import engines as eng


@pytest.fixture()
def pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把 pin 映射文件指向临时目录（避免污染真实 user_data_dir）。"""
    from openkg_webui.services import path_service

    class _PS:
        user_data_dir = tmp_path

    monkeypatch.setattr(path_service, "get_path_service", lambda: _PS())
    return tmp_path


# ---------------------------------------------------------------------------
# 注册表与路由解析（P2-2）
# ---------------------------------------------------------------------------


def test_engine_id_falls_back_to_default(pinned) -> None:
    assert eng.engine_id_for_dataset(None) == eng.DEFAULT_ENGINE_ID
    assert eng.engine_id_for_dataset("kb-unknown") == eng.DEFAULT_ENGINE_ID


def test_pin_and_unpin_roundtrip(pinned) -> None:
    eng.pin_dataset_engine("kb-1", "some-other-engine")
    assert eng.engine_id_for_dataset("kb-1") == "some-other-engine"
    # 其他库不受影响
    assert eng.engine_id_for_dataset("kb-2") == eng.DEFAULT_ENGINE_ID
    eng.unpin_dataset_engine("kb-1")
    assert eng.engine_id_for_dataset("kb-1") == eng.DEFAULT_ENGINE_ID


def test_build_engine_rejects_unknown_id(pinned) -> None:
    with pytest.raises(eng.EngineError):
        eng.build_engine("does-not-exist")


def test_build_engine_returns_intellect_rag(pinned) -> None:
    engine = eng.build_engine()
    assert engine.engine_id == eng.ENGINE_INTELLECT_RAG
    assert engine.display_name


# ---------------------------------------------------------------------------
# 能力与协议（P3 / P1-1）
# ---------------------------------------------------------------------------


def test_capabilities_are_declared_constants(pinned) -> None:
    engine = eng.build_engine()
    declared = {
        eng.CAP_UPLOAD,
        eng.CAP_STRUCTURED_UPLOAD,
        eng.CAP_SEARCH,
        eng.CAP_DELETE,
        eng.CAP_SOURCES,
        eng.CAP_LOGS,
        eng.CAP_PREVIEW,
        eng.CAP_CHAT_BINDING,
        eng.CAP_MCP_BINDING,
    }
    assert engine.capabilities <= declared
    # intellect-rag 支持全部已声明能力
    assert engine.capabilities == declared


def test_provider_exposes_protocol_methods(pinned) -> None:
    engine = eng.build_engine()
    for name in ("request", "upload", "chat_binding", "mcp_binding"):
        assert callable(getattr(engine, name)), name


# ---------------------------------------------------------------------------
# 聊天侧绑定三态（P1-1）
# ---------------------------------------------------------------------------


def test_chat_binding_disabled_returns_none(pinned, monkeypatch) -> None:
    import openkg_webui.services.knowledge as ks

    monkeypatch.setattr(ks, "get_knowledge_settings", lambda: {"enabled": False})
    assert eng.build_engine().chat_binding() is None


def test_chat_binding_without_selection_uses_scope(pinned, monkeypatch) -> None:
    import openkg_webui.services.knowledge as ks

    monkeypatch.setattr(
        ks, "get_knowledge_settings", lambda: {"enabled": True, "chat_scope": "team"}
    )
    assert eng.build_engine().chat_binding() == {"enabled": True, "scope": "team"}
    assert eng.build_engine().chat_binding([]) == {"enabled": True, "scope": "team"}


def test_chat_binding_with_selection_pins_kb_ids(pinned, monkeypatch) -> None:
    import openkg_webui.services.knowledge as ks

    monkeypatch.setattr(
        ks, "get_knowledge_settings", lambda: {"enabled": True, "chat_scope": "team"}
    )
    assert eng.build_engine().chat_binding(["kb-1", "kb-2"]) == {
        "enabled": True,
        "knowledge_base_ids": ["kb-1", "kb-2"],
    }


# ---------------------------------------------------------------------------
# 服务层委托（保持既有调用方签名稳定）
# ---------------------------------------------------------------------------


def test_service_layer_delegates_to_engine(pinned, monkeypatch) -> None:
    import openkg_webui.services.knowledge as ks

    monkeypatch.setattr(
        ks, "get_knowledge_settings", lambda: {"enabled": True, "chat_scope": "tenant"}
    )
    # services.knowledge.chat_rag_block 必须与引擎结果一致（签名兼容既有调用方）
    assert ks.chat_rag_block() == eng.build_engine().chat_binding()
    assert ks.chat_rag_block(["kb-x"]) == {"enabled": True, "knowledge_base_ids": ["kb-x"]}
