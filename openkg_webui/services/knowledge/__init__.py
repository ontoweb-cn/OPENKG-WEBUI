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


def _defaults_file() -> Any:
    """per-user 默认知识库存储（A3）：``<user_data_dir>/knowledge_defaults.json``。

    以 user_id 为键的映射（值为 dataset_id 列表）——dataset_id 跨身份模式
    稳定，因此该偏好不受 header/token 归因切换影响（评审 R6/D4 修订）。
    """
    from pathlib import Path

    from openkg_webui.services.path_service import get_path_service

    return Path(get_path_service().user_data_dir) / "knowledge_defaults.json"


def default_knowledge_kb_ids(user_id: str) -> list[str]:
    """该用户的默认知识库（A3，dataset ids）；未设置返回 ``[]``。"""
    import json

    try:
        path = _defaults_file()
        if not path.exists():
            return []
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return []
    value = data.get(str(user_id or "")) or []
    return [str(x) for x in value] if isinstance(value, list) else []


def set_default_knowledge_dataset(user_id: str, dataset_id: str | None) -> None:
    """设置/清除该用户的默认知识库（A3）。``dataset_id=None`` 清除。"""
    import json

    path = _defaults_file()
    data: dict[str, Any] = {}
    try:
        if path.exists():
            data = json.loads(path.read_text("utf-8")) or {}
    except Exception:
        data = {}
    uid = str(user_id or "")
    if not uid:
        raise ValueError("user id is required")
    if dataset_id:
        data[uid] = [str(dataset_id)]
    else:
        data.pop(uid, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    import os
    import tempfile

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".defaults-", suffix=".tmp")
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


def ensure_knowledge_mcp_config(workdir: str) -> None:
    """CLI 后端 MCP 注入（Phase 2 T6）。

    向 session workdir 的 ``.mcp.json`` **合并** intellect-knowledge server
    （streamable-http，指向 ``mcp_url``），并在 ``.claude/settings.json``
    放行 ``intellect_retrieval``。凭据：header 模式 = 服务 key；token 模式 =
    该用户 member token（与 turn 检索同一身份源，P1-1）。写入失败不阻塞
    turn；kag 的 ensure_session_mcp_config 可能已写同一文件——读取-合并-写回。
    """
    import json
    from pathlib import Path

    block = get_knowledge_settings()
    if not block.get("enabled") or not block.get("mcp_url"):
        return
    try:
        # 请求上下文内解析当前用户身份（P1-1：与 turn 检索同一身份源）
        bearer, identity_headers = resolve_request_auth()
    except Exception:
        return
    mcp_entry = {
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
    servers["intellect-knowledge"] = mcp_entry
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
    permissions = dict(settings.get("permissions") or {})
    allow = [str(x) for x in (permissions.get("allow") or [])]
    wanted = "mcp__intellect-knowledge__intellect_retrieval"
    if wanted not in allow:
        allow.append(wanted)
    permissions["allow"] = allow
    settings["permissions"] = permissions
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", "utf-8")


def chat_rag_block() -> dict[str, Any] | None:
    """runs 协议请求体的 ``rag`` 会话块（网关 ``build_session_config`` 契约）。

    knowledge 未启用时返回 ``None``——调用方不带该键，请求体与既有部署
    逐字节一致。Phase 1b 只带 ``scope``（租户级默认召回，决策 D2）；per-会话
    ``knowledge_base_ids`` 属 Phase 1.5（B2），网关侧字段已就绪。
    """
    block = get_knowledge_settings()
    if not block.get("enabled"):
        return None
    return {"enabled": True, "scope": str(block.get("chat_scope") or "tenant")}


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
