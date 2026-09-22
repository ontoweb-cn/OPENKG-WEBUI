# -*- coding: utf-8 -*-
"""知识引擎抽象（docs/plans/knowledge-center-phase-3-engine-abstraction.md §二）。

设计要点（2026-09-23 技术评审修订版）：

1. **三层接入面各有一个方法**——管理面 ``request``/``upload``、聊天面
   ``chat_binding``、MCP 面 ``mcp_binding``。只抽象管理面会漏掉聊天与 MCP
   的引擎耦合（评审 P1-1：``chat_rag_block`` 是 intellect-team 网关专有契约）。
2. **按请求实例化**：身份是每请求解析的（P1-1 要求与 turn 检索同一身份源），
   因此 provider 由 :class:`EngineContext` 绑定上下文后构造（评审 P2-3）。
3. **能力用集合表达**（评审 P3）：``capabilities`` 是 ``CAP_*`` 常量的子集，
   UI 据此显示/隐藏操作；不做继承层次与运行时协商。
4. **T2 阶段保持透传语义**：``request``/``upload`` 返回上游 ``Response``
   （信封不变 → 前端不变）；域模型归一化属 T3（评审 P1-2 的表述修正）。

模块边界：``engines/`` 只依赖 ``services.knowledge`` 的公共函数（迟导入避免
循环），不反向被其内部实现依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# 引擎 id 与能力（评审 P3：取值需为常量，UI 才能 switch）
# ---------------------------------------------------------------------------

ENGINE_INTELLECT_RAG = "intellect-rag"

#: 能力常量——UI 按能力显示/隐藏操作（如 KAG 无上传文档语义）。
CAP_UPLOAD = "upload"                    # 多文件上传
CAP_STRUCTURED_UPLOAD = "structured_upload"  # zip/文件夹结构化上传
CAP_SEARCH = "search"                    # 检索试玩
CAP_DELETE = "delete"                    # 删除库/文档
CAP_SOURCES = "sources"                  # 外部源（GitHub/Web 同步）
CAP_LOGS = "logs"                        # 解析日志流
CAP_PREVIEW = "preview"                  # 文档预览/缩略图
CAP_CHAT_BINDING = "chat_binding"        # 参与聊天召回（runs rag 块）
CAP_MCP_BINDING = "mcp_binding"          # 提供会话 MCP 注入


class EngineErrorKind:
    """``EngineError`` 的分类（路由层据此映射 HTTP 状态，评审 P3 的错误模型）。"""

    UNREACHABLE = "unreachable"              # 传输层失败 → 502
    UPSTREAM_ERROR = "upstream_error"        # 其他上游异常 → 502


class EngineError(RuntimeError):
    """引擎调用失败；``kind`` 供路由层做 HTTP 映射。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class EngineContext:
    """一次请求的引擎上下文（评审 P2-3：身份随上下文绑定，方法不再各自带 user_id）。

    ``user_id=None`` 表示"由身份层从请求上下文取当前用户"（同步任务等后台
    场景显式传入）。
    """

    user_id: str | None = None
    language: str = "en"
    #: 预留：调用方（路由层）可注入的额外元数据，provider 不应依赖其内容。
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadItem:
    """一个待上传文件（结构化上传与外部源同步共用）。"""

    name: str
    content: bytes
    content_type: str = "application/octet-stream"


@runtime_checkable
class KnowledgeEngine(Protocol):
    """知识引擎 provider 接口。

    T2 契约：``request``/``upload`` 返回上游 ``Response``（含信封与业务状态码），
    路由层原样透传——对外行为与重构前逐字节一致。T3 将升级为域模型。
    """

    engine_id: str
    display_name: str
    capabilities: frozenset[str]

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: list[tuple[str, Any]] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """执行一次带身份的上游请求（``path`` 为引擎内相对路径）。"""
        ...

    async def upload(
        self,
        dataset_id: str,
        items: list[UploadItem],
        *,
        parent_path: str = "",
        upload_type: str = "local",
    ) -> Any:
        """上传一组文件到指定知识库（路由与外部源同步共用，评审 P2-1）。"""
        ...

    def chat_binding(self, kb_ids: list[str] | None = None) -> dict[str, Any] | None:
        """本引擎的聊天侧绑定产物（intellect-rag: runs ``rag`` 块）。

        返回 ``None`` = 该引擎不参与聊天侧绑定（评审 P1-1）。
        """
        ...

    def mcp_binding(self, workdir: str) -> None:
        """会话 MCP 注入（写 session workdir 的 ``.mcp.json`` 等）。

        不提供 MCP 的引擎实现为空操作（评审 P1-1）。
        """
        ...
