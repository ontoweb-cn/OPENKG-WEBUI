# -*- coding: utf-8 -*-
"""intellect-rag 引擎 provider（Phase 3 T2：从代理路由与 services.knowledge
原样迁移，行为不变）。

迁移来源：
- ``request``/``upload`` ← ``api/routers/knowledge.py`` 的 ``_upstream`` 与
  上传/同步任务的 httpx 调用；
- ``chat_binding`` ← ``services/knowledge/__init__.py`` 的 ``chat_rag_block``；
- ``mcp_binding`` ← 同文件的 ``ensure_knowledge_mcp_config``。

T2 保持透传语义（返回上游 ``Response``，信封与业务状态码原样），T3 再升级为
域模型；鉴权与身份策略沿用 P1-1（与 agent-loop 同一身份源）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

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
    UploadItem,
)

#: 管理面流量：连接 60s / 读流 300s（大文档 preview）。
UPSTREAM_TIMEOUT = httpx.Timeout(60.0, read=300.0)

#: MCP server 条目名与工具名（chat 会话的 ``.mcp.json`` / 权限放行使用）。
MCP_SERVER_NAME = "intellect-knowledge"
MCP_TOOL_NAME = "intellect_retrieval"


class IntellectRagEngine:
    """intellect-rag-app（REST ``/api/v1/*``）引擎 provider。"""

    engine_id = ENGINE_INTELLECT_RAG
    display_name = "Intellect RAG"
    capabilities = frozenset(
        {
            CAP_UPLOAD,
            CAP_STRUCTURED_UPLOAD,
            CAP_SEARCH,
            CAP_DELETE,
            CAP_SOURCES,
            CAP_LOGS,
            CAP_PREVIEW,
            CAP_CHAT_BINDING,
            CAP_MCP_BINDING,
        }
    )

    def __init__(self, ctx: EngineContext | None = None, *, transport: Any = None) -> None:
        self._ctx = ctx or EngineContext()
        #: 测试注入点（httpx.MockTransport）；生产恒为 None。
        self._transport = transport

    # -- 内部：配置与身份 ------------------------------------------------

    def _connection(self) -> tuple[str, str]:
        from openkg_webui.services.knowledge import resolve_upstream_connection

        return resolve_upstream_connection()

    def _auth(self) -> tuple[str, dict[str, str]]:
        from openkg_webui.services.knowledge import resolve_request_auth

        return resolve_request_auth(self._ctx.user_id)

    def _client(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{base_url}/api/v1",
            timeout=UPSTREAM_TIMEOUT,
            transport=self._transport,
        )

    # -- 管理面 ----------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        files: list[tuple[str, Any]] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """执行一次带身份的上游请求并返回响应（含信封与业务状态码）。"""
        base_url, _ = self._connection()
        bearer, identity_headers = self._auth()
        # Authorization 放最后：profile 自带 headers（operator 为 team 服务配置的
        # 定制头，可能含 Authorization）不得覆盖发往 rag-app 的凭据（评审 R-4）。
        headers = {**identity_headers, "Authorization": f"Bearer {bearer}"}
        try:
            async with self._client(base_url) as client:
                return await client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    files=files,
                    data=data,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise EngineError(
                EngineErrorKind.UNREACHABLE, f"knowledge upstream unreachable: {exc}"
            ) from exc

    async def upload(
        self,
        dataset_id: str,
        items: list[UploadItem],
        *,
        parent_path: str = "",
        upload_type: str = "local",
    ) -> httpx.Response:
        """上传一组文件到指定知识库（上游单请求支持多文件；``parent_path``
        为存储前缀，分组由调用方负责）。"""
        payload = [
            ("file", (item.name, item.content, item.content_type)) for item in items
        ]
        data: dict[str, Any] = {"type": upload_type}
        if parent_path:
            data["parent_path"] = parent_path
        return await self.request(
            "POST", f"/datasets/{dataset_id}/documents", files=payload, data=data
        )

    # -- 聊天面（P1-1：本引擎的 runs ``rag`` 块，intellect-team 网关契约）--

    def chat_binding(self, kb_ids: list[str] | None = None) -> dict[str, Any] | None:
        """runs 协议请求体的 ``rag`` 会话块（网关 ``build_session_config`` 契约）。

        knowledge 未启用时返回 ``None``——调用方不带该键，请求体与既有部署
        逐字节一致。``kb_ids`` 非空时携带 ``knowledge_base_ids``（Phase 1.5），
        否则回落到部署默认 ``chat_scope``（Phase 1b）。
        """
        from openkg_webui.services.knowledge import get_knowledge_settings

        block = get_knowledge_settings()
        if not block.get("enabled"):
            return None
        if kb_ids:
            return {"enabled": True, "knowledge_base_ids": list(kb_ids)}
        return {"enabled": True, "scope": str(block.get("chat_scope") or "tenant")}

    # -- MCP 面 ----------------------------------------------------------

    def mcp_binding(self, workdir: str) -> None:
        """CLI 后端 MCP 注入（Phase 2 T6）。

        向 session workdir 的 ``.mcp.json`` **合并** server 条目
        （streamable-http，指向 ``mcp_url``），并在 ``.claude/settings.json``
        放行检索工具。凭据：header 模式 = 服务 key；token 模式 = 该用户
        member token（与 turn 检索同一身份源，P1-1）。写入失败不阻塞 turn；
        kag 的 ``ensure_session_mcp_config`` 可能已写同一文件——读取-合并-写回。
        """
        from openkg_webui.services.knowledge import get_knowledge_settings

        block = get_knowledge_settings()
        if not block.get("enabled") or not block.get("mcp_url"):
            return
        try:
            bearer, identity_headers = self._auth()
        except Exception:
            return
        mcp_entry = {
            # Claude Code 要求 url 型条目带 "type"，否则整个 server 被跳过
            # （2026-09-22 claude mcp list 实测："has a url but no type"）
            "type": "http",
            "url": str(block.get("mcp_url")),
            "headers": {
                "Authorization": f"Bearer {bearer}",
                **{
                    k: v
                    for k, v in identity_headers.items()
                    if k.lower() != "authorization"
                },
            },
        }
        workdir_path = Path(workdir)
        mcp_path = workdir_path / ".mcp.json"
        config: dict[str, Any] = {"mcpServers": {}}
        if mcp_path.exists():
            try:
                config = json.loads(mcp_path.read_text("utf-8")) or {"mcpServers": {}}
            except Exception:
                config = {"mcpServers": {}}
        servers = dict(config.get("mcpServers") or {})
        servers[MCP_SERVER_NAME] = mcp_entry
        config["mcpServers"] = servers
        mcp_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", "utf-8")
        # .mcp.json 含 Bearer 凭据——收紧为仅属主可读（工作区在服务器侧）
        try:
            import os as _os

            _os.chmod(mcp_path, 0o600)
        except OSError:
            pass

        claude_dir = workdir_path / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_path = claude_dir / "settings.json"
        settings: dict[str, Any] = {}
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text("utf-8")) or {}
            except Exception:
                settings = {}
        # Claude Code 两道门（沿 kag ensure_session_mcp_config 的实测结论）：
        #   enableAllProjectMcpServers —— 项目级 MCP server 审批
        #   permissions.allow —— 非交互模式下的工具级 permission 门
        settings["enableAllProjectMcpServers"] = True
        permissions = dict(settings.get("permissions") or {})
        allow = [str(x) for x in (permissions.get("allow") or [])]
        wanted = f"mcp__{MCP_SERVER_NAME}__{MCP_TOOL_NAME}"
        if wanted not in allow:
            allow.append(wanted)
        permissions["allow"] = allow
        settings["permissions"] = permissions
        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
