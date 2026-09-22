# -*- coding: utf-8 -*-
"""引擎注册表与 dataset → 引擎解析（评审 P2-2）。

解析规则（可执行口径）：

1. **pin 映射命中** → 该引擎（``<user_data_dir>/knowledge_engines.json``，
   形如 ``{"<dataset_id>": "<engine_id>"}``，创建库时写入）；
2. **未命中** → 默认引擎 :data:`DEFAULT_ENGINE_ID`（Phase 3 仅一个引擎，
   第二个引擎落地时改为读 settings）。

当前只有 intellect-rag 一个 provider，pin 映射是**为第二个引擎预置的路由
地基**：现有 KB 不写 pin，解析回落默认引擎，行为与重构前一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import ENGINE_INTELLECT_RAG

DEFAULT_ENGINE_ID = ENGINE_INTELLECT_RAG


def _pins_file() -> Path:
    from openkg_webui.services.path_service import get_path_service

    return Path(get_path_service().user_data_dir) / "knowledge_engines.json"


def _load_pins() -> dict[str, str]:
    path = _pins_file()
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if str(v)}


def _save_pins(data: dict[str, str]) -> None:
    import os
    import tempfile

    path = _pins_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".engines-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def pin_dataset_engine(dataset_id: str, engine_id: str) -> None:
    """记录某个知识库所属引擎（创建库时调用；引擎切换的路由依据）。"""
    if not dataset_id:
        raise ValueError("dataset_id is required")
    data = _load_pins()
    data[str(dataset_id)] = str(engine_id)
    _save_pins(data)


def unpin_dataset_engine(dataset_id: str) -> None:
    """删除库时清理 pin（幂等）。"""
    data = _load_pins()
    if str(dataset_id) in data:
        data.pop(str(dataset_id), None)
        _save_pins(data)


def engine_id_for_dataset(dataset_id: str | None) -> str:
    """解析某知识库所属引擎；未 pin 或为空 → 默认引擎。"""
    if not dataset_id:
        return DEFAULT_ENGINE_ID
    return _load_pins().get(str(dataset_id)) or DEFAULT_ENGINE_ID


def build_context(
    *, user_id: str | None = None, language: str = "en", **metadata: Any
) -> Any:
    """构造 :class:`~.base.EngineContext`（薄封装，便于路由层单点调用）。"""
    from .base import EngineContext

    return EngineContext(user_id=user_id, language=language, metadata=dict(metadata))


def build_engine(engine_id: str | None = None, *, ctx: Any = None, transport: Any = None) -> Any:
    """按 id 构造 provider 实例（当前仅 intellect-rag；未知 id 明确报错）。

    第二个引擎落地时在此扩展——加引擎 = 加 provider + 加一个分支。
    """
    from .base import ENGINE_INTELLECT_RAG, EngineContext, EngineError
    from .intellect_rag import IntellectRagEngine

    context = ctx if ctx is not None else EngineContext()
    resolved = engine_id or DEFAULT_ENGINE_ID
    if resolved == ENGINE_INTELLECT_RAG:
        return IntellectRagEngine(context, transport=transport)
    raise EngineError(
        "upstream_error", f"unknown knowledge engine: {resolved}"
    )
