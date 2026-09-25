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

#: 主体归因头。契约名与 ``services/agent_loop/identity.py`` 的 ``_HEADER_USER``
#: 一致（该模块是身份桥接的唯一决策点）；此处以字面量复用以避免跨模块引用
#: 私有名——两处的同步由 `tests/services/knowledge/test_request_auth.py::
#: test_header_literal_matches_identity_module` 守卫。
#:
#: 另注：``identity.py`` 的 member 路径**有意丢弃**该头（"sending one would
#: only be misleading"），因为对 turn 目标服务而言令牌即身份。知识中心是
#: 另一个下游（rag-app 的 owner 短路需要主体 id），所以这里把它补回来——
#: 不是与上游决策冲突，而是服务不同目标。
_IDENTITY_USER_HEADER = "X-Intellect-User"


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
    """CLI 后端 MCP 注入——委托默认引擎的 ``mcp_binding``（评审 P1-1）。

    引擎差异（intellect-rag 写 ``.mcp.json`` 的 intellect-knowledge 条目；
    KAG 写 kag-bridge 或不注入）由 provider 实现。
    """
    from openkg_webui.services.knowledge.engines import build_engine

    build_engine().mcp_binding(workdir)


def chat_rag_block(kb_ids: list[str] | None = None) -> dict[str, Any] | None:
    """runs 协议请求体的 ``rag`` 会话块——委托默认引擎的 ``chat_binding``。

    保留模块级签名以稳定既有调用方（`agent_loop/http_backend.py`）；
    引擎差异（如 KAG 走 grounding 块）由 provider 实现（评审 P1-1）。
    """
    from openkg_webui.services.knowledge.engines import build_engine

    return build_engine().chat_binding(kb_ids)


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

    - ``token``/``token_required`` **且已链接**：Bearer = 该用户的 member token
      （rag-app 的 imt_ 鉴权路径接受），headers 携带 X-Intellect-User（主体）
      与 X-Intellect-Team/Project（链接记录有值时才带）；
    - ``header``/``off`` 模式：Bearer = 服务 key（与 team 同一把），headers
      携带 X-Intellect-User/Team/Project 做归因。

    注意 ``token`` 模式下**未链接**的用户会降级为归因形态（``tier="header"``、
    ``degraded=True``），此时 Bearer 是**服务 key**——权限比 member token 大。
    本函数原样返回该降级结果（turn 路径的既有语义，见 identity.py 模块文档），
    因此调用方不能用"Bearer 非空"推断"身份是受限的"。``token_required`` 模式
    不会降级：未链接直接抛 :class:`KnowledgeIdentityUnavailable`。

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
    headers = dict(getattr(identity, "headers", None) or {})
    if mode in {"token", "token_required"}:
        bearer = str(getattr(identity, "api_key", "") or "")
        if not bearer:
            raise KnowledgeIdentityUnavailable("Linked identity token missing.")
        # 主体钉定：token 模式不携带 X-Intellect-User 时，rag-app 侧的主体
        # 会退化为 X-Intellect-Tenant（resolve_tenant_id）的解析值——owner
        # 短路与取数租户集合都会用错身份（设了 INTELLECT_TENANT_ID 的部署里
        # private 库会整体不可见，并集修复也会以错误的 basis 扩大可见集）。
        # 这里补发 member token 自己的 member_id。
        #
        # 安全：rag-app 的 resolve_subject_id → bind_subject_id 会把与已认证
        # 主体不一致的头钳制为已认证主体（imt_ 路径不享服务 key/可信 BFF 的
        # 透传豁免），且 sync_membership 的 token 路径忽略该头做身份——
        # 因此补发既不能让调用方冒充他人，也不改变成员关系写入。
        #
        # 命名空间一致性（2026-09-25 复核）：rag-app 解析出的主体是 authed_id
        # （`verify_member_token` → GET /api/members/me 的 id/member_id），
        # 与本函数补发的值来自**同一个端点**，因此同一次令牌委托下二者相等，
        # 主体不会落到另一命名空间。上游对旧行另有 fallback owner 短路兜底。
        member_id = str(getattr(identity, "member_id", "") or "")
        if member_id and not any(
            k.lower() == _IDENTITY_USER_HEADER.lower() for k in headers
        ):
            headers[_IDENTITY_USER_HEADER] = member_id
    else:
        bearer = service_key
    return bearer, headers
