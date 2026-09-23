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
    """``EngineError`` 的分类（路由层据此映射 HTTP 状态）。

    映射表（T3 方案 §三，按评审修订版）：
        unreachable    → 502   传输失败/超时
        upstream_error → 502   上游 5xx / 未归类失败
        unauthorized   → 403   认证/权限被拒（含 RetCode 108/109）
        not_found      → 404   资源不存在
        invalid        → 400   参数/请求错误（含 RetCode 101）
    """

    UNREACHABLE = "unreachable"
    UPSTREAM_ERROR = "upstream_error"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    INVALID = "invalid"


#: 上游 RetCode（common/constants.py）中承载语义的取值。
UPSTREAM_SUCCESS = 0
UPSTREAM_EXCEPTION = 100
UPSTREAM_ARGUMENT_ERROR = 101
UPSTREAM_DATA_ERROR = 102  # 重载码：权限/存在性/参数三类语义共用
UPSTREAM_CONNECTION_ERROR = 105
UPSTREAM_PERMISSION_ERROR = 108
UPSTREAM_AUTHENTICATION_ERROR = 109


def classify_upstream(code: int | None, message: str = "") -> str | None:
    """把上游信号归类为 :class:`EngineErrorKind`；``None`` = 无错误。

    102 是重载码（实测：``"No authorization"`` / ``"lacks permission"`` 与
    ``"Document not found"`` / ``"Invalid filename"`` 同走该码），因此必须按
    message 关键词细分——否则权限拒绝会被误报为"不存在"（评审 P1）。
    """
    if code in (None, UPSTREAM_SUCCESS):
        return None
    text = (message or "").lower()
    if code in (UPSTREAM_PERMISSION_ERROR, UPSTREAM_AUTHENTICATION_ERROR):
        return EngineErrorKind.UNAUTHORIZED
    if code in (UPSTREAM_ARGUMENT_ERROR,):
        return EngineErrorKind.INVALID
    if code in (UPSTREAM_EXCEPTION, UPSTREAM_CONNECTION_ERROR):
        return (
            EngineErrorKind.UNREACHABLE
            if code == UPSTREAM_CONNECTION_ERROR
            else EngineErrorKind.UPSTREAM_ERROR
        )
    if code == UPSTREAM_DATA_ERROR:
        if "not found" in text or "not exist" in text or "no such" in text:
            return EngineErrorKind.NOT_FOUND
        if "permission" in text or "no authorization" in text or "unauthorized" in text:
            return EngineErrorKind.UNAUTHORIZED
        return EngineErrorKind.INVALID
    return EngineErrorKind.UPSTREAM_ERROR


class EngineError(RuntimeError):
    """引擎调用失败。

    携带上游原始信号（评审 P4）供路由层映射与日志归因：
    ``upstream_code`` = 信封 code，``upstream_status`` = HTTP 状态码。
    """

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        upstream_code: int | None = None,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.upstream_code = upstream_code
        self.upstream_status = upstream_status


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
