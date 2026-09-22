# -*- coding: utf-8 -*-
"""知识引擎包（Phase 3）：协议、注册表、provider 实现。

布局（评审 P3 的模块边界）：
- ``base``     —— 协议 / 域常量 / 错误模型 / EngineContext（无外部依赖）
- ``registry`` —— 引擎解析（dataset pin 映射 + 默认引擎）与实例构造
- ``intellect_rag`` —— intellect-rag-app provider

依赖方向：``engines → services.knowledge``（迟导入）单向；``services.knowledge``
对引擎的引用同样迟导入，避免环。
"""

from .base import (
    CAP_CHAT_BINDING,
    CAP_DELETE,
    CAP_LOGS,
    CAP_MCP_BINDING,
    CAP_PREVIEW,
    CAP_SEARCH,
    CAP_SOURCES,
    CAP_STRUCTURED_UPLOAD,
    CAP_UPLOAD,
    ENGINE_INTELLECT_RAG,
    EngineContext,
    EngineError,
    EngineErrorKind,
    KnowledgeEngine,
    UploadItem,
)
from .registry import (
    DEFAULT_ENGINE_ID,
    build_context,
    build_engine,
    engine_id_for_dataset,
    pin_dataset_engine,
    unpin_dataset_engine,
)

__all__ = [
    "CAP_CHAT_BINDING",
    "CAP_DELETE",
    "CAP_LOGS",
    "CAP_MCP_BINDING",
    "CAP_PREVIEW",
    "CAP_SEARCH",
    "CAP_SOURCES",
    "CAP_STRUCTURED_UPLOAD",
    "CAP_UPLOAD",
    "DEFAULT_ENGINE_ID",
    "ENGINE_INTELLECT_RAG",
    "EngineContext",
    "EngineError",
    "EngineErrorKind",
    "KnowledgeEngine",
    "UploadItem",
    "build_context",
    "build_engine",
    "engine_id_for_dataset",
    "pin_dataset_engine",
    "unpin_dataset_engine",
]
