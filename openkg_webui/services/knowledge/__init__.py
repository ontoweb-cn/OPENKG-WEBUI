# -*- coding: utf-8 -*-
"""知识中心服务层（docs/knowledge-center-port-design.md §三/§九；Phase 1a）。

职责：``knowledge`` 设置块读取、rag-app 上游连接解析、以及与 agent-loop
同源的身份解析（P1-1：UI 管理面与 turn 检索必须走同一身份派生）。

上游 intellect-rag-app 与 agent-loop 的目标服务（intellect-team）是两个
进程：连接配置（base_url/api_key）在本模块的 settings 块维护，api_key
取值与 team 部署的 INTELLECT_RAG_API_KEY 同一把（设计 §十一-2）；身份
header 一律经 agent-loop identity 解析，本模块不维护第二套用户映射。
"""

from __future__ import annotations

from typing import Any


class KnowledgeNotConfigured(Exception):
    """knowledge 未启用，或上游连接（base_url/api_key）未配置。"""


class KnowledgeIdentityUnavailable(Exception):
    """当前用户无法解析出与 agent-loop 一致的上游身份（未链接/过期/漂移）。"""


def get_knowledge_settings() -> dict[str, Any]:
    """读取归一化后的 ``knowledge`` settings 块（异常/缺省返回空 dict）。"""
    try:
        from openkg_webui.services.config.runtime_settings import RuntimeSettingsService

        system = RuntimeSettingsService.get_instance().load_system()
    except Exception:
        return {}
    block = system.get("knowledge")
    return dict(block) if isinstance(block, dict) else {}


def knowledge_enabled(block: dict[str, Any] | None = None) -> bool:
    """功能开关（不含连接完备性——连接问题在请求时以 409 暴露给设置页引导）。"""
    block = block if block is not None else get_knowledge_settings()
    return bool(block.get("enabled"))


def resolve_upstream_connection() -> tuple[str, str]:
    """返回 rag-app 上游 ``(base_url, service_api_key)``；未配置抛
    :class:`KnowledgeNotConfigured`。"""
    block = get_knowledge_settings()
    if not block.get("enabled"):
        raise KnowledgeNotConfigured("knowledge integration is disabled.")
    base_url = str(block.get("base_url") or "").rstrip("/")
    api_key = str(block.get("api_key") or "")
    if not base_url or not api_key:
        raise KnowledgeNotConfigured("knowledge upstream connection is not configured.")
    return base_url, api_key


def resolve_request_auth(user_id: str | None = None) -> tuple[str, dict[str, str]]:
    """解析一次上游请求的 ``(Bearer token, 附加身份 headers)``。

    身份派生完全复用 agent-loop 的 :func:`resolve_backend_identity`（P1-1）：

    - ``token``/``token_required`` 模式：Bearer = 该用户的 member token
      （rag-app 的 imt_ 鉴权路径接受），headers 携带 X-Intellect-Team/Project；
    - ``header``/``off`` 模式：Bearer = 服务 key（与 team 同一把），headers
      携带 X-Intellect-User/Team/Project 做归因。

    ``user_id=None`` 时由 identity 层从请求上下文取当前用户。
    """
    from openkg_webui.services.agent_loop.identity import (
        IdentityUnavailable,
        normalize_identity_mode,
        resolve_backend_identity,
    )
    from openkg_webui.services.agent_loop.settings import resolve_primary_profile

    _, service_key = resolve_upstream_connection()
    profile = resolve_primary_profile()
    if not profile:
        raise KnowledgeIdentityUnavailable("No agent-loop profile configured.")
    try:
        identity = resolve_backend_identity(profile, family="http", user_id=user_id)
    except IdentityUnavailable as exc:
        raise KnowledgeIdentityUnavailable(str(exc)) from exc
    if identity is None:
        raise KnowledgeIdentityUnavailable("Identity resolution returned nothing.")
    mode = normalize_identity_mode(profile.get("identity_mode"))
    if mode in {"token", "token_required"}:
        bearer = str(getattr(identity, "api_key", "") or "")
        if not bearer:
            raise KnowledgeIdentityUnavailable("Linked identity token missing.")
    else:
        bearer = service_key
    return bearer, dict(getattr(identity, "headers", None) or {})
