# -*- coding: utf-8 -*-
"""知识中心管理面 REST（docs/knowledge-center-port-design.md §三；Phase 1a T3）。

薄代理：``/api/knowledge/*`` 与 intellect-rag-app ``/api/v1/*`` 一一镜像
（决策 Q1/D4），形状翻译在前端 parse 层。本模块只做四件事：

1. 会话鉴权（挂载时统一 ``_auth``）与 knowledge 启用门；
2. 身份注入——经 agent-loop identity（services.knowledge.resolve_request_auth）
   按当前用户解析 Bearer + X-Intellect-*，与 turn 检索同一身份源（P1-1）；
3. 写操作 same-origin guard（沿 kag.py 模式）；
4. 传输层错误归一 502；上游业务状态码/``{code, data, message}`` 信封原样
   透传（thin——上游 403/404 语义对前端有意义，不做二次包装）。

不落地任何知识域数据——rag-app 是唯一权威源（设计 §三分工原则）。
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from openkg_webui.services.knowledge import (
    KnowledgeIdentityUnavailable,
    KnowledgeNotConfigured,
    get_knowledge_settings,
    knowledge_enabled,
    resolve_request_auth,
    resolve_upstream_connection,
)

router = APIRouter()

#: 管理面流量：连接 60s / 读流 300s（大文档 preview）。
_UPSTREAM_TIMEOUT = httpx.Timeout(60.0, read=300.0)

#: 测试注入点（httpx.MockTransport）；生产恒为 None（走默认 transport）。
_transport: httpx.AsyncBaseTransport | None = None


def _current_user() -> Any:
    from openkg_webui.multi_user.context import get_current_user

    return get_current_user()


def _user_id() -> str | None:
    return str(getattr(_current_user(), "id", "") or "") or None


def _require_enabled() -> None:
    if not knowledge_enabled():
        raise HTTPException(status_code=403, detail="Knowledge center is not available.")


def _require_same_origin(request: Request) -> None:
    from openkg_webui.services.config.origins import origin_is_trusted, request_authority
    from openkg_webui.services.config.runtime_settings import load_system_settings

    try:
        system = load_system_settings()
        allowed = [
            str(system.get("cors_origin") or ""),
            *(str(x) for x in (system.get("cors_origins") or [])),
        ]
    except Exception:
        allowed = []
    # 浏览器原始 host 优先（Next rewrite 追加 X-Forwarded-Host，见 origins 模块）
    if not origin_is_trusted(
        request.headers.get("origin"),
        request_authority(
            request.headers.get("host"), request.headers.get("x-forwarded-host")
        ),
        allowed,
    ):
        raise HTTPException(status_code=403, detail="Cross-site request refused.")


def _service_error(exc: Exception) -> HTTPException:
    """设置/身份层错误 → 409（带机器可读 code 供设置页引导）；其余 502。"""
    if isinstance(exc, KnowledgeNotConfigured):
        return HTTPException(status_code=409, detail=f"knowledge_not_configured: {exc}")
    if isinstance(exc, KnowledgeIdentityUnavailable):
        return HTTPException(status_code=409, detail=f"knowledge_identity_unavailable: {exc}")
    return HTTPException(status_code=502, detail=f"knowledge_upstream_error: {exc}")


async def _upstream(
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
    files: list[tuple[str, Any]] | None = None,
    data: dict[str, Any] | None = None,
) -> Response:
    """执行一次带身份的上游请求并原样透传响应（含信封与业务状态码）。"""
    try:
        base_url, _ = resolve_upstream_connection()
        bearer, identity_headers = resolve_request_auth(_user_id())
    except (KnowledgeNotConfigured, KnowledgeIdentityUnavailable) as exc:
        raise _service_error(exc) from exc
    headers = {"Authorization": f"Bearer {bearer}", **identity_headers}
    try:
        async with httpx.AsyncClient(
            base_url=f"{base_url}/api/v1",
            timeout=_UPSTREAM_TIMEOUT,
            transport=_transport,
        ) as client:
            upstream = await client.request(
                method,
                path,
                json=json,
                params=params,
                files=files,
                data=data,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"knowledge_upstream_unreachable: {exc}"
        ) from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


async def _json_body(request: Request) -> Any:
    try:
        return await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body.") from exc


# ---------------------------------------------------------------------------
# 状态（前端门控：未启用/身份不可用时入口隐藏、页面给设置引导）
# ---------------------------------------------------------------------------


@router.get("")
async def knowledge_status() -> dict[str, Any]:
    block = get_knowledge_settings()
    enabled = knowledge_enabled(block)
    identity_ok: bool | None = None
    if enabled:
        try:
            resolve_request_auth(_user_id())
            identity_ok = True
        except Exception:
            identity_ok = False
    return {"enabled": enabled, "identity_ok": identity_ok}


# ---------------------------------------------------------------------------
# Datasets（知识库）
# ---------------------------------------------------------------------------


@router.get("/datasets")
async def knowledge_list_datasets(request: Request) -> Response:
    _require_enabled()
    return await _upstream("GET", "/datasets", params=dict(request.query_params))


@router.post("/datasets")
async def knowledge_create_dataset(request: Request) -> Response:
    _require_enabled()
    _require_same_origin(request)
    return await _upstream("POST", "/datasets", json=await _json_body(request))


@router.get("/datasets/{dataset_id}")
async def knowledge_get_dataset(dataset_id: str) -> Response:
    _require_enabled()
    return await _upstream("GET", f"/datasets/{dataset_id}")


@router.delete("/datasets/{dataset_id}")
async def knowledge_delete_dataset(dataset_id: str, request: Request) -> Response:
    _require_enabled()
    _require_same_origin(request)
    return await _upstream("DELETE", f"/datasets/{dataset_id}")


# ---------------------------------------------------------------------------
# Documents（文档）
# ---------------------------------------------------------------------------


@router.get("/datasets/{dataset_id}/documents")
async def knowledge_list_documents(dataset_id: str, request: Request) -> Response:
    _require_enabled()
    return await _upstream(
        "GET", f"/datasets/{dataset_id}/documents", params=dict(request.query_params)
    )


@router.post("/datasets/{dataset_id}/documents")
async def knowledge_upload_documents(
    dataset_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    type_: str | None = Form(None, alias="type"),
    parent_path: str | None = Form(None),
) -> Response:
    """多文件上传透传。``UploadFile.file`` 是磁盘回退的 SpooledTemporaryFile，
    httpx 在异步上下文中经线程池分块读取，不会整体载入内存（风险注记见任务清单 §七）。"""
    _require_enabled()
    _require_same_origin(request)
    payload = [
        ("files", (f.filename or "file", f.file, f.content_type or "application/octet-stream"))
        for f in files
    ]
    data: dict[str, Any] = {"type": type_ or "local"}
    if parent_path:
        data["parent_path"] = parent_path
    return await _upstream(
        "POST", f"/datasets/{dataset_id}/documents", files=payload, data=data
    )


@router.delete("/datasets/{dataset_id}/documents")
async def knowledge_delete_documents(dataset_id: str, request: Request) -> Response:
    _require_enabled()
    _require_same_origin(request)
    return await _upstream(
        "DELETE", f"/datasets/{dataset_id}/documents", json=await _json_body(request)
    )


@router.post("/datasets/{dataset_id}/documents/parse")
async def knowledge_parse_documents(dataset_id: str, request: Request) -> Response:
    _require_enabled()
    _require_same_origin(request)
    return await _upstream(
        "POST", f"/datasets/{dataset_id}/documents/parse", json=await _json_body(request)
    )


@router.post("/datasets/{dataset_id}/documents/stop")
async def knowledge_stop_documents(dataset_id: str, request: Request) -> Response:
    _require_enabled()
    _require_same_origin(request)
    return await _upstream(
        "POST", f"/datasets/{dataset_id}/documents/stop", json=await _json_body(request)
    )


@router.get("/datasets/{dataset_id}/documents/{document_id}")
async def knowledge_get_document(dataset_id: str, document_id: str) -> Response:
    _require_enabled()
    return await _upstream("GET", f"/datasets/{dataset_id}/documents/{document_id}")


# ---------------------------------------------------------------------------
# 摄取记录 / 检索试玩 / 预览
# ---------------------------------------------------------------------------


@router.get("/datasets/{dataset_id}/ingestions")
async def knowledge_list_ingestions(dataset_id: str, request: Request) -> Response:
    _require_enabled()
    return await _upstream(
        "GET", f"/datasets/{dataset_id}/ingestions", params=dict(request.query_params)
    )


@router.get("/datasets/{dataset_id}/ingestions/{log_id}")
async def knowledge_get_ingestion(dataset_id: str, log_id: str) -> Response:
    _require_enabled()
    return await _upstream("GET", f"/datasets/{dataset_id}/ingestions/{log_id}")


@router.post("/datasets/{dataset_id}/search")
async def knowledge_search_dataset(dataset_id: str, request: Request) -> Response:
    _require_enabled()
    return await _upstream(
        "POST", f"/datasets/{dataset_id}/search", json=await _json_body(request)
    )


@router.get("/documents/{document_id}/preview")
async def knowledge_preview_document(document_id: str) -> Response:
    _require_enabled()
    return await _upstream("GET", f"/documents/{document_id}/preview")


@router.get("/thumbnails")
async def knowledge_thumbnail(request: Request) -> Response:
    _require_enabled()
    return await _upstream("GET", "/thumbnails", params=dict(request.query_params))
