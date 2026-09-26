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

import asyncio
from collections import defaultdict
import io
import json
import posixpath
import re
from typing import Any
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
import httpx

from openkg_webui.services.knowledge import (
    KnowledgeIdentityUnavailable,
    KnowledgeNotConfigured,
    get_knowledge_settings,
    knowledge_enabled,
)
from openkg_webui.services.knowledge.engines.models import (
    ChunkPage,
    DatasetPage,
    DocumentPage,
    IngestionLog,
    KnowledgeDataset,
    KnowledgeDocument,
    KnowledgeGraph,
    MutationResult,
    SearchResult,
    StructuredUploadResult,
    UploadResult,
)
from openkg_webui.services.knowledge.engines.registry import (
    DEFAULT_ENGINE_ID,
    engine_id_for_dataset,
)

#: 上游资源 id（dataset/document/chunk）均为服务端生成的短 id——路径与查询里
#: 出现其它字符即可判定为探测/路径混淆（评审安全 S1：id 进入上游 URL 路径
#: 前统一校验，防 %2F/.. 拼接出未预期的上游端点）。
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ID_PARAM_KEYS = frozenset({"dataset_id", "document_id", "chunk_id", "doc_id", "log_id"})


async def _validate_resource_ids(request: Request) -> None:
    for key in _ID_PARAM_KEYS:
        value = request.path_params.get(key) or request.query_params.get(key)
        if value is not None and not _SAFE_ID_RE.match(str(value)):
            raise HTTPException(status_code=400, detail=f"invalid {key}")


router = APIRouter(dependencies=[Depends(_validate_resource_ids)])

#: 测试注入点（httpx.MockTransport）；生产恒为 None（走默认 transport）。
_transport: httpx.AsyncBaseTransport | None = None

# ── 结构化上传（Phase 2 T1）安全上限 ────────────────────────────────
_ZIP_MAX_ENTRIES = 500
_ZIP_MAX_TOTAL_BYTES = 200 * 1024 * 1024
_ZIP_MAX_FILE_BYTES = 50 * 1024 * 1024
#: 每个目录一次上游请求——目录数上限防止深目录结构放大成请求风暴。
_ZIP_MAX_DIRS = 200


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
        request_authority(request.headers.get("host"), request.headers.get("x-forwarded-host")),
        allowed,
    ):
        raise HTTPException(status_code=403, detail="Cross-site request refused.")


def _http_error(exc: Exception) -> HTTPException:
    """统一错误 → HTTP 映射（T3 方案 §三，按评审修订版）。

    配置/身份层错误保持 409（设置页引导语义，1a 既有约定）；引擎错误按
    :class:`EngineError.kind` 映射 403/404/400/502。上游 message 原文随
    detail 透出供 UI 展示。
    """
    from openkg_webui.services.knowledge.engines import EngineError, EngineErrorKind

    if isinstance(exc, KnowledgeNotConfigured):
        return HTTPException(status_code=409, detail=f"knowledge_not_configured: {exc}")
    if isinstance(exc, KnowledgeIdentityUnavailable):
        return HTTPException(status_code=409, detail=f"knowledge_identity_unavailable: {exc}")
    if isinstance(exc, EngineError):
        mapping = {
            EngineErrorKind.UNAUTHORIZED: 403,
            EngineErrorKind.NOT_FOUND: 404,
            EngineErrorKind.INVALID: 400,
            EngineErrorKind.UNREACHABLE: 502,
            EngineErrorKind.UPSTREAM_ERROR: 502,
        }
        status_code = mapping.get(exc.kind, 502)
        return HTTPException(status_code=status_code, detail=f"knowledge_{exc.kind}: {exc}")
    return HTTPException(status_code=502, detail=f"knowledge_upstream_error: {exc}")


def _engine_for(dataset_id: str | None = None) -> Any:
    """构造本次请求的引擎 provider（按 dataset pin 路由；评审 P2-2/P2-3）。

    身份通过 :class:`EngineContext` 随请求绑定——provider 内部复用同一份
    agent-loop 身份解析（P1-1）。
    """
    from openkg_webui.services.knowledge.engines import build_context, build_engine

    ctx = build_context(user_id=_user_id())
    return build_engine(engine_id_for_dataset(dataset_id), ctx=ctx, transport=_transport)


async def _engine_call(op: Any, *, dataset_id: str | None = None) -> Any:
    """执行一次引擎调用并把异常归一为 HTTP（T3：路由只做门控与映射）。

    ``op`` 是接收 provider 的协程函数：
    ``await _engine_call(lambda e: e.list_documents(ds), dataset_id=ds)``
    """
    from openkg_webui.services.knowledge.engines import EngineError

    engine = _engine_for(dataset_id)
    try:
        return await op(engine)
    except (KnowledgeNotConfigured, KnowledgeIdentityUnavailable, EngineError) as exc:
        raise _http_error(exc) from exc


def _visibility_from_headers(headers: dict[str, str] | None) -> str:
    """新建知识库**实际**会得到的可见范围："private" | "team" | "project"。

    判定依据是「本次请求实际会携带的归因头」——与上游创建时写入
    ``visibility`` 的输入同源（``build_ownership_fields`` 读
    ``subject_context.team_id/project_id``，即 X-Intellect-Team/Project）。

    为什么返回字符串而不是"能否共享"的布尔值（评审 P1-1）：上游**忽略**
    创建请求体里的 ``permission``，visibility 完全由头决定。所以
    - 无 Team/Project 头 → 只能建 private（请求里的 team 无效）；
    - 有 Team 或 Project 头 → 只能建 team/project（请求里的 me 无效）。
    两个方向都不可选，因此 UI 应当**陈述**范围而不是让用户选一个兑现不了的值。
    注意 Project 头单独存在时上游也落 ``visibility="project"``
    （``_compute_visibility`` 的三分支），只查 Team 会漏判。

    传入 headers 而非 engine，是为了让调用方只解析一次身份（评审 P2-4：
    每次 _auth() 都要重读 system.json 与凭据文件）。
    """
    lowered = {str(key).lower(): str(value or "").strip() for key, value in (headers or {}).items()}
    if lowered.get("x-intellect-team"):
        return "team"
    if lowered.get("x-intellect-project"):
        return "project"
    return "private"


def _int_param(params: Any, key: str, default: int, *, lo: int = 1, hi: int = 1000) -> int:
    """查询分页参数容错：非数字/越界回退默认（评审质量 Q1，防 500）。"""
    try:
        value = int(params.get(key) or default)
    except (TypeError, ValueError):
        return default
    return max(lo, min(value, hi))


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
    create_visibility = "private"
    if enabled:
        try:
            # Phase 3 T2：身份可用性经默认引擎的 provider 校验（P1-1 同一身份源）。
            # 解析成功即可用：token 模式缺 bearer 时 resolve_request_auth 会抛错，
            # header/off 模式以服务 key 认证。此处的返回值不再被真值化（评审 P2-4：
            # 对元组取 bool 恒为 True，读起来像检查实际没检查）。
            _bearer, headers = _engine_for(None)._auth()
            identity_ok = True
            create_visibility = _visibility_from_headers(headers)
        except Exception:
            identity_ok = False
    return {
        "enabled": enabled,
        "identity_ok": identity_ok,
        "create_visibility": create_visibility,
    }


# ---------------------------------------------------------------------------
# per-user 默认知识库（A3，Phase 1.5 T4）
# ---------------------------------------------------------------------------


@router.get("/preferences")
async def knowledge_get_preferences() -> dict[str, Any]:
    _require_enabled()
    from openkg_webui.services.knowledge import default_knowledge_kb_ids

    ids = default_knowledge_kb_ids(_user_id())
    return {"default_dataset_id": ids[0] if ids else None}


@router.put("/preferences")
async def knowledge_put_preferences(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    _require_enabled()
    _require_same_origin(request)
    from openkg_webui.services.knowledge import (
        default_knowledge_kb_ids,
        set_default_knowledge_dataset,
    )

    dataset_id = str((payload or {}).get("default_dataset_id") or "").strip() or None
    set_default_knowledge_dataset(_user_id(), dataset_id)
    ids = default_knowledge_kb_ids(_user_id())
    return {"default_dataset_id": ids[0] if ids else None}


# ---------------------------------------------------------------------------
# Datasets（知识库）
# ---------------------------------------------------------------------------


@router.get("/datasets", response_model=DatasetPage)
async def knowledge_list_datasets(request: Request, page: int = 1, page_size: int = 30) -> Any:
    _require_enabled()
    q = request.query_params
    page = await _engine_call(
        lambda e: e.list_datasets(
            page=_int_param(q, "page", page),
            page_size=_int_param(q, "page_size", page_size),
        )
    )
    for ds in page.datasets:
        ds.engine_id = engine_id_for_dataset(ds.id)
    return page


@router.post("/datasets", response_model=KnowledgeDataset)
async def knowledge_create_dataset(request: Request) -> Any:
    _require_enabled()
    _require_same_origin(request)
    body = await _json_body(request)
    payload = body if isinstance(body, dict) else {}

    requested = str(payload.get("permission") or "").strip().lower()
    if requested and requested not in {"me", "team"}:
        # 上游 CreateDatasetReq.permission 是 Literal["me","team"]，非法值会
        # 在那边 400。此前本层把任意非法值静默折叠成 "me" 并"成功"建库——
        # 用户以为生效、实际被忽略。改为同口径拒绝（评审 P3-4）。
        raise HTTPException(status_code=400, detail="Invalid permission value.")

    async def _create(engine: Any) -> Any:
        # legacy `permission` 只是创建时的期望值，**上游不据此计算 visibility**
        # （由 Team/Project 头决定，见 _visibility_from_headers）。因此它必须与
        # 实际范围一致，否则留下"看着是团队、实际私有"（或反向）的记录。
        # 无团队范围时钳制为 me；有则即便请求说 me 上游也会落 team/project，
        # 这里如实上报（评审 P1-1）。
        _bearer, headers = engine._auth()
        permission = "team" if _visibility_from_headers(headers) in {"team", "project"} else "me"
        dataset = await engine.create_dataset(
            name=str(payload.get("name") or ""),
            description=str(payload.get("description") or ""),
            permission=permission,
        )
        # 引擎 pin（T1/T2 遗留）：多引擎路由的地基；单引擎下行为中性
        from openkg_webui.services.knowledge.engines import pin_dataset_engine

        if getattr(dataset, "id", ""):
            pin_dataset_engine(dataset.id, engine.engine_id)
        return dataset

    return await _engine_call(_create)


@router.get("/datasets/{dataset_id}", response_model=KnowledgeDataset)
async def knowledge_get_dataset(dataset_id: str) -> Any:
    _require_enabled()
    dataset = await _engine_call(lambda e: e.get_dataset(dataset_id), dataset_id=dataset_id)
    dataset.engine_id = engine_id_for_dataset(dataset_id)
    return dataset


@router.put("/datasets/{dataset_id}", response_model=KnowledgeDataset)
async def knowledge_update_dataset(dataset_id: str, request: Request) -> Any:
    """部分更新知识库（P1-T8）：name/description 可选，回读返回服务端状态。

    ``embedding_model``（P3 T13'）走 **D5 强制检查**：服务端先跑兼容性探针，
    不兼容直接 409——不信任客户端预先检查过。
    """
    _require_enabled()
    _require_same_origin(request)
    body = await _json_body(request)
    payload = body if isinstance(body, dict) else {}

    name = payload.get("name")
    description = payload.get("description")
    embedding_model = payload.get("embedding_model")
    if name is None and description is None and embedding_model is None:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    if name is not None and not str(name).strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty.")

    async def _update(engine: Any) -> Any:
        if embedding_model is not None:
            target = str(embedding_model).strip()
            check = await engine.check_embedding_compatibility(dataset_id, target)
            if not check.compatible:
                raise HTTPException(
                    status_code=409,
                    detail=f"embedding_incompatible: {check.reason}",
                )
            return await engine.update_dataset(
                dataset_id,
                name=None if name is None else str(name).strip(),
                description=None if description is None else str(description),
                embedding_model=target,
            )
        return await engine.update_dataset(
            dataset_id,
            name=None if name is None else str(name).strip(),
            description=None if description is None else str(description),
        )

    return await _engine_call(_update, dataset_id=dataset_id)


@router.delete("/datasets/{dataset_id}")
async def knowledge_delete_dataset(dataset_id: str, request: Request) -> Any:
    _require_enabled()
    _require_same_origin(request)

    async def _delete(engine: Any) -> dict[str, Any]:
        await engine.delete_dataset(dataset_id)
        from openkg_webui.services.knowledge.engines import unpin_dataset_engine

        unpin_dataset_engine(dataset_id)
        return {"deleted": True, "dataset_id": dataset_id}

    return await _engine_call(_delete, dataset_id=dataset_id)


# ---------------------------------------------------------------------------
# Documents（文档）
# ---------------------------------------------------------------------------


@router.get("/datasets/{dataset_id}/documents", response_model=DocumentPage)
async def knowledge_list_documents(dataset_id: str, request: Request) -> Any:
    _require_enabled()
    q = request.query_params
    return await _engine_call(
        lambda e: e.list_documents(
            dataset_id,
            page=_int_param(q, "page", 1),
            page_size=_int_param(q, "page_size", 100),
        ),
        dataset_id=dataset_id,
    )


@router.post("/datasets/{dataset_id}/documents", response_model=UploadResult)
async def knowledge_upload_documents(
    dataset_id: str,
    request: Request,
    # 字段名必须是 file：前端 FormData 与上游 getlist("file") 都用它
    # （评审 R-1 + 联调修正——参数名即 multipart 字段名，不能叫 files）。
    file: list[UploadFile] = File(...),
    type_: str | None = Form(None, alias="type"),
    parent_path: str | None = Form(None),
) -> Response:
    """多文件上传透传。``UploadFile.file`` 是磁盘回退的 SpooledTemporaryFile，
    httpx 在异步上下文中经线程池分块读取，不会整体载入内存（风险注记见任务清单 §七）。"""
    _require_enabled()
    _require_same_origin(request)
    # D4 流式契约：直传路径传 **file-like**（SpooledTemporaryFile），由 httpx
    # 分块读取——不得读入内存（1a 显式设计，任务清单 §七风险注记）。
    from openkg_webui.services.knowledge.engines import UploadItem

    items = [
        UploadItem(
            name=f.filename or "file",
            content=f.file,  # type: ignore[arg-type]
            content_type=f.content_type or "application/octet-stream",
        )
        for f in file
    ]
    return await _engine_call(
        lambda e: e.upload(
            dataset_id,
            items,
            parent_path=parent_path or "",
            upload_type=type_ or "local",
        ),
        dataset_id=dataset_id,
    )


# ---------------------------------------------------------------------------
# 结构化上传（Phase 2 T1/D1/D2）：zip 解包 + per-file 相对路径 + 目录分组
# ---------------------------------------------------------------------------


def _safe_zip_entries(data: bytes) -> list[tuple[str, bytes]]:
    """解包 zip 为 ``(相对路径, 内容)`` 列表，带安全防护：

    - zip-slip：拒绝绝对路径与含 ``..`` 段的条目；
    - 炸弹熔断：条目数 / 总解压体积 / 单文件体积上限；
    - 跳过目录项、``__MACOSX`` 与点开头（隐藏）文件/目录。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"invalid zip: {exc}") from exc
    with zf:
        names = zf.namelist()
        if len(names) > _ZIP_MAX_ENTRIES:
            raise HTTPException(status_code=413, detail="zip: too many entries")
        out: list[tuple[str, bytes]] = []
        total = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            raw = info.filename.replace("\\", "/")
            if raw.startswith("/") or ".." in raw.split("/"):
                raise HTTPException(status_code=400, detail=f"unsafe zip entry: {raw}")
            parts = [p for p in raw.split("/") if p]
            if any(p.startswith(".") for p in parts) or any(p == "__MACOSX" for p in parts):
                continue
            # 炸弹熔断必须在解压前完成：zip 头声明大小可伪造（
            # 压缩比炸弹），但读入内存前先拒绝明显超限的条目已能挡住
            # 绝大多数情况；读入后再以真实大小复核（双保险）。
            declared = int(getattr(info, "file_size", 0) or 0)
            if declared > _ZIP_MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"zip: entry too large: {info.filename}",
                )
            if total + declared > _ZIP_MAX_TOTAL_BYTES:
                raise HTTPException(status_code=413, detail="zip: total size exceeds limit")
            try:
                # 按声明大小 + 1 字节读取上限，避免声明值被伪造时读爆内存
                with zf.open(info) as handle:
                    content = handle.read(_ZIP_MAX_FILE_BYTES + 1)
            except RuntimeError as exc:
                # 加密条目等 zipfile 层错误——按不可用条目拒绝整个 zip
                raise HTTPException(
                    status_code=400, detail=f"zip entry unreadable: {info.filename}"
                ) from exc
            if len(content) > _ZIP_MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=413, detail=f"zip: entry too large: {info.filename}"
                )
            total += len(content)
            if total > _ZIP_MAX_TOTAL_BYTES:
                raise HTTPException(status_code=413, detail="zip: total size exceeds limit")
            out.append(("/".join(parts), content))
    if not out:
        raise HTTPException(status_code=400, detail="zip: no usable entries")
    return out


def _group_by_dir(entries: list[tuple[str, ...]]) -> dict[str, list[tuple[str, ...]]]:
    """按父目录分组（上游单请求仅支持单一 parent_path）。"""
    groups: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for entry in entries:
        groups[posixpath.dirname(entry[0])].append(entry)
    return groups


@router.post(
    "/datasets/{dataset_id}/documents/structured",
    response_model=list[StructuredUploadResult],
)
async def knowledge_upload_structured(
    dataset_id: str,
    request: Request,
    file: list[UploadFile] = File(...),
    rel_paths: str | None = Form(None),
    type_: str | None = Form(None, alias="type"),
) -> Response:
    """结构化上传（zip / 文件夹）：解包 zip、按 ``rel_paths`` 保留目录结构，
    按目录分组后逐组转发上游（每目录一次请求，`parent_path`=目录）。

    - ``rel_paths``：与 ``file`` 对齐的相对路径 JSON 数组（文件夹上传语义）；
      未提供时 zip 条目用其内部路径、普通文件用文件名。
    - 响应为各分组上游结果的数组（信封透传）。
    """
    _require_enabled()
    _require_same_origin(request)

    plain: list[tuple[str, UploadFile]] = []
    zips: list[UploadFile] = []
    rel_list: list[str] = []
    if rel_paths:
        try:
            parsed = json.loads(rel_paths)
            rel_list = [str(x) for x in parsed] if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="invalid rel_paths") from exc

    for index, f in enumerate(file):
        name = f.filename or "file"
        if name.lower().endswith(".zip"):
            zips.append(f)
        else:
            rel = rel_list[index] if index < len(rel_list) else name
            # rel_paths 来自浏览器（webkitRelativePath），必须与服务端 zip 条目
            # 同等清洗：去首斜杠、拒绝 ``..`` 段（否则可写到 KB 目录之外）。
            rel = rel.replace("\\", "/").lstrip("/")
            if ".." in rel.split("/"):
                raise HTTPException(status_code=400, detail="unsafe rel_path")
            plain.append((rel, f))

    expanded: list[tuple[str, bytes, str]] = []
    for zf in zips:
        data = await zf.read()
        for path, content in _safe_zip_entries(data):
            expanded.append((path, content, "application/octet-stream"))
    for rel, f in plain:
        content = await f.read()
        expanded.append((rel, content, f.content_type or "application/octet-stream"))

    if not expanded:
        raise HTTPException(status_code=400, detail="no files to upload")

    groups = _group_by_dir(expanded)
    if len(groups) > _ZIP_MAX_DIRS:
        raise HTTPException(
            status_code=413,
            detail=f"too many directories ({len(groups)} > {_ZIP_MAX_DIRS})",
        )
    # T3：聚合为域形状（取代 {directory, status, upstream} 的信封嵌套）。
    # 逐目录容错（评审 P1 同一原则）：单目录失败不中断其余目录。
    from openkg_webui.services.knowledge.engines import (
        EngineError,
        StructuredUploadResult,
        UploadItem,
    )

    engine = _engine_for(dataset_id)
    results: list[StructuredUploadResult] = []
    for directory, items in sorted(groups.items()):
        payload_items = [
            UploadItem(name=posixpath.basename(name), content=content, content_type=ctype)
            for name, content, ctype in items
        ]
        try:
            uploaded = await engine.upload(
                dataset_id,
                payload_items,
                parent_path=directory or "",
                upload_type=type_ or "local",
            )
            results.append(StructuredUploadResult(directory=directory, uploaded=uploaded.uploaded))
        except (EngineError, KnowledgeNotConfigured, KnowledgeIdentityUnavailable) as exc:
            results.append(StructuredUploadResult(directory=directory, error=str(exc)))
    return results


# ---------------------------------------------------------------------------
# 外部源·GitHub（Phase 2 T4；同步语义移植自 DeepMentor services/github_source，
# Apache-2.0——配置/状态存 openkg-webui 侧，内容经既有上传链路入库）
# ---------------------------------------------------------------------------


@router.put("/datasets/{dataset_id}/sources/github")
async def knowledge_put_github_source(
    dataset_id: str, request: Request, payload: dict[str, Any]
) -> dict[str, Any]:
    _require_enabled()
    _require_same_origin(request)
    from openkg_webui.services.knowledge.sources import store

    repo = str(payload.get("repo") or "").strip()
    if not repo or "/" not in repo:
        raise HTTPException(status_code=400, detail="repo must look like owner/name")
    cfg = {
        "repo": repo,
        "branch": str(payload.get("branch") or "").strip() or "main",
        "path_prefix": str(payload.get("path_prefix") or "").strip().strip("/"),
        "glob": str(payload.get("glob") or "").strip() or "*.md",
        "token": str(payload.get("token") or "").strip(),
    }
    store.set_source(_user_id(), dataset_id, cfg)
    return store.get_source(_user_id(), dataset_id) or {}


@router.get("/datasets/{dataset_id}/sources/github")
async def knowledge_get_github_source(dataset_id: str) -> dict[str, Any]:
    _require_enabled()
    from openkg_webui.services.knowledge.sources import store

    source = store.get_source(_user_id(), dataset_id)
    if source is None:
        raise HTTPException(status_code=404, detail="github source not configured")
    return source


@router.delete("/datasets/{dataset_id}/sources/github")
async def knowledge_delete_github_source(dataset_id: str, request: Request) -> dict[str, Any]:
    _require_enabled()
    _require_same_origin(request)
    from openkg_webui.services.knowledge.sources import store

    deleted = store.delete_source(_user_id(), dataset_id)
    return {"deleted": deleted}


async def _run_github_sync(user_id: str, dataset_id: str) -> None:
    """后台同步任务：plan → 上传变更 → 删除移除项 → 更新状态。"""
    import os

    from openkg_webui.services.knowledge.sources import github, store

    cfg = store.get_source(user_id, dataset_id) or {}
    client = github.GitHubClient(
        token=store.get_source_token(user_id, dataset_id) or os.environ.get("GITHUB_TOKEN")
    )
    try:
        prev_files = {
            k: v for k, v in (store.get_state(user_id, dataset_id).get("files") or {}).items()
        }
        plan = await github.plan_sync(
            client,
            repo=cfg.get("repo", ""),
            branch=cfg.get("branch") or "main",
            path_prefix=cfg.get("path_prefix", ""),
            glob=cfg.get("glob") or "*.md",
            prev_files=prev_files,
        )
        if plan.skipped:
            store.update_state(
                user_id, dataset_id, {"sync_status": "done", "last_result": "no changes"}
            )
            return
        # Phase 3 T2（评审 P2-1）：经引擎 provider 上传，不再自行拼引擎 REST——
        # 否则引擎切换时外部源同步会静默打到旧引擎。身份由 EngineContext 绑定，
        # 后台任务显式传 user_id。
        from openkg_webui.services.knowledge.engines import (
            UploadItem,
            build_context,
            build_engine,
        )

        engine = build_engine(
            engine_id_for_dataset(dataset_id),
            ctx=build_context(user_id=user_id),
            transport=_transport,
        )
        uploaded = 0
        if True:
            for path, content in plan.uploads:
                directory = posixpath.dirname(path)
                resp = await engine.upload(
                    dataset_id,
                    [
                        UploadItem(
                            name=posixpath.basename(path),
                            content=content,
                            content_type="application/octet-stream",
                        )
                    ],
                    parent_path=directory,
                )
                if resp.status_code < 400:
                    uploaded += 1
            removed = 0
            if plan.removals:
                listed = await engine.request(
                    "GET",
                    f"/datasets/{dataset_id}/documents",
                    params={"page": 1, "page_size": 100},
                )
                docs = (
                    (listed.json().get("data") or {}).get("docs") or []
                    if listed.status_code == 200
                    else []
                )
                # 上传写入的文档名是 basename（见上方 files_payload），
                # 因此删除匹配也必须用 basename——否则远端删除的文档永远
                # 删不掉（原实现按仓库全路径查 name，恒空）。
                name_to_id = {d.get("name"): d.get("id") for d in docs if isinstance(d, dict)}
                doomed = [
                    name_to_id[posixpath.basename(p)]
                    for p in plan.removals
                    if name_to_id.get(posixpath.basename(p))
                ]
                if doomed:
                    resp = await engine.request(
                        "DELETE",
                        f"/datasets/{dataset_id}/documents",
                        json={"ids": doomed},
                    )
                    removed += len(doomed) if resp.status_code < 400 else 0
        # plan.files = 同步后远端全量 {path: sha}——增量状态以此为准（评审 F2）
        store.update_state(
            user_id,
            dataset_id,
            {
                "sync_status": "done",
                "last_commit": plan.head,
                "files": plan.files,
                "last_result": f"uploaded {uploaded}, removed {removed}",
            },
        )
    except Exception as exc:
        store.update_state(
            user_id, dataset_id, {"sync_status": "error", "last_result": str(exc)[:300]}
        )
    finally:
        # 任务被取消/异常退出时不得遗留 running 状态（阻塞后续同步）
        state = store.get_state(user_id, dataset_id)
        if state.get("sync_status") == "running":
            store.set_status(user_id, dataset_id, "interrupted")


@router.post("/datasets/{dataset_id}/sources/github/sync")
async def knowledge_sync_github_source(dataset_id: str, request: Request) -> dict[str, Any]:
    _require_enabled()
    _require_same_origin(request)
    from openkg_webui.services.knowledge.sources import store

    if store.get_source(_user_id(), dataset_id) is None:
        raise HTTPException(status_code=404, detail="github source not configured")
    if store.get_state(_user_id(), dataset_id).get("sync_status") == "running":
        return {"started": False, "reason": "sync already running"}
    user_id = _user_id() or ""
    try:
        # 预校验：配置/身份不可用时立刻 409（而不是任务里静默失败）
        _engine_for(dataset_id)._connection()
    except (KnowledgeNotConfigured, KnowledgeIdentityUnavailable) as exc:
        raise _http_error(exc) from exc
    store.set_status(user_id, dataset_id, "running")
    asyncio.create_task(_run_github_sync(user_id, dataset_id))
    return {"started": True}


# ---------------------------------------------------------------------------
# 外部源·Web 爬取（Phase 2 T5）：同站 BFS 抓取 → HTML 转 Markdown → 增量入库
# ---------------------------------------------------------------------------


def _slug_for_url(url: str) -> str:
    from openkg_webui.services.knowledge.sources.web import url_slug

    return url_slug(url)


@router.put("/datasets/{dataset_id}/sources/web")
async def knowledge_put_web_source(
    dataset_id: str, request: Request, payload: dict[str, Any]
) -> dict[str, Any]:
    _require_enabled()
    _require_same_origin(request)
    from openkg_webui.services.knowledge.sources import store
    from openkg_webui.services.knowledge.sources.web import is_public_http_url

    base_url = str(payload.get("base_url") or "").strip()
    if not is_public_http_url(base_url):
        raise HTTPException(status_code=400, detail="base_url must be a public http(s) url")
    cfg = {
        "type": "web",
        "base_url": base_url.rstrip("/"),
        "max_pages": max(1, min(int(payload.get("max_pages") or 20), 100)),
        "max_depth": max(1, min(int(payload.get("max_depth") or 2), 5)),
    }
    store.set_source(_user_id(), dataset_id, cfg)
    return store.get_source(_user_id(), dataset_id) or {}


@router.get("/datasets/{dataset_id}/sources/web")
async def knowledge_get_web_source(dataset_id: str) -> dict[str, Any]:
    _require_enabled()
    from openkg_webui.services.knowledge.sources import store

    source = store.get_source(_user_id(), dataset_id)
    if source is None or (source.get("type") != "web"):
        raise HTTPException(status_code=404, detail="web source not configured")
    return source


@router.delete("/datasets/{dataset_id}/sources/web")
async def knowledge_delete_web_source(dataset_id: str, request: Request) -> dict[str, Any]:
    _require_enabled()
    _require_same_origin(request)
    from openkg_webui.services.knowledge.sources import store

    deleted = store.delete_source(_user_id(), dataset_id)
    return {"deleted": deleted}


async def _run_web_sync(user_id: str, dataset_id: str) -> None:
    from openkg_webui.services.knowledge.sources import store, web

    cfg = store.get_source(user_id, dataset_id) or {}
    try:
        pages = await web.crawl_site(
            cfg.get("base_url", ""),
            max_pages=int(cfg.get("max_pages") or 20),
            max_depth=int(cfg.get("max_depth") or 2),
        )
        prev = {k: v for k, v in (store.get_state(user_id, dataset_id).get("pages") or {}).items()}
        current = {page.url: page.content_hash for page in pages}
        uploads = [p for p in pages if prev.get(p.url) != p.content_hash]
        removals = [u for u in prev if u not in current]

        # Phase 3 T2（评审 P2-1）：经引擎 provider，不再自行拼引擎 REST。
        from openkg_webui.services.knowledge.engines import (
            UploadItem,
            build_context,
            build_engine,
        )

        engine = build_engine(
            engine_id_for_dataset(dataset_id),
            ctx=build_context(user_id=user_id),
            transport=_transport,
        )
        uploaded = 0
        if True:
            for page in uploads:
                name = _slug_for_url(page.url)
                resp = await engine.upload(
                    dataset_id,
                    [
                        UploadItem(
                            name=name,
                            content=page.markdown.encode("utf-8"),
                            content_type="text/markdown",
                        )
                    ],
                    parent_path=f"web/{_host_of(page.url)}",
                )
                if resp.status_code < 400:
                    uploaded += 1
            if removals:
                listed = await engine.request(
                    "GET",
                    f"/datasets/{dataset_id}/documents",
                    params={"page": 1, "page_size": 100},
                )
                docs = (
                    (listed.json().get("data") or {}).get("docs") or []
                    if listed.status_code == 200
                    else []
                )
                name_to_id = {d.get("name"): d.get("id") for d in docs if isinstance(d, dict)}
                doomed = []
                for url in removals:
                    doc_id = name_to_id.get(_slug_for_url(url))
                    if doc_id:
                        doomed.append(doc_id)
                if doomed:
                    await engine.request(
                        "DELETE",
                        f"/datasets/{dataset_id}/documents",
                        json={"ids": doomed},
                    )
        store.update_state(
            user_id,
            dataset_id,
            {
                "sync_status": "done",
                "pages": current,
                "last_result": f"uploaded {uploaded}, removed {len(removals)}, crawled {len(pages)}",
            },
        )
    except Exception as exc:
        store.update_state(
            user_id, dataset_id, {"sync_status": "error", "last_result": str(exc)[:300]}
        )
    finally:
        state = store.get_state(user_id, dataset_id)
        if state.get("sync_status") == "running":
            store.set_status(user_id, dataset_id, "interrupted")


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).hostname or "web"


@router.post("/datasets/{dataset_id}/sources/web/sync")
async def knowledge_sync_web_source(dataset_id: str, request: Request) -> dict[str, Any]:
    _require_enabled()
    _require_same_origin(request)
    from openkg_webui.services.knowledge.sources import store

    source = store.get_source(_user_id(), dataset_id)
    if source is None or source.get("type") != "web":
        raise HTTPException(status_code=404, detail="web source not configured")
    if store.get_state(_user_id(), dataset_id).get("sync_status") == "running":
        return {"started": False, "reason": "sync already running"}
    user_id = _user_id() or ""
    try:
        _engine_for(dataset_id)._connection()
    except (KnowledgeNotConfigured, KnowledgeIdentityUnavailable) as exc:
        raise _http_error(exc) from exc
    store.set_status(user_id, dataset_id, "running")
    asyncio.create_task(_run_web_sync(user_id, dataset_id))
    return {"started": True}


# ---------------------------------------------------------------------------
# 解析日志流（Phase 2 T3）：代理侧 SSE——服务端轮询上游 ingestions，
# 快照变化即推送；客户端断开或超时退出。
# ---------------------------------------------------------------------------


@router.get("/datasets/{dataset_id}/logs/stream")
async def knowledge_stream_logs(
    dataset_id: str,
    request: Request,
    max_ticks: int = 300,
):
    _require_enabled()
    # Phase 3 T2：经引擎 provider 轮询（身份随 EngineContext 绑定）。
    # 连接错误在首次调用时暴露为 409/502（与其余端点一致）。
    from openkg_webui.services.knowledge.engines import EngineError

    engine = _engine_for(dataset_id)
    try:
        engine._connection()  # 提前校验配置：未配置时以 409 拒绝而非静默空流
    except (KnowledgeNotConfigured, KnowledgeIdentityUnavailable) as exc:
        raise _http_error(exc) from exc
    max_ticks = max(1, min(max_ticks, 300))  # 生产上限 10 分钟；测试可调小

    from fastapi.responses import StreamingResponse

    async def generator():
        last_signature: str | None = None
        ticks = 0
        while ticks < max_ticks:
            ticks += 1
            if await request.is_disconnected():
                return
            try:
                # T3（D6）：日志条目域化——上游 progress_msg/operation_status
                # 的映射在 provider 内完成，流内只出现域形状。
                entries = await engine.list_ingestions(
                    dataset_id, log_type="file", page=1, page_size=5
                )
                logs = [entry.model_dump() for entry in entries]
                signature = json.dumps(logs, ensure_ascii=False, sort_keys=True)
                if signature != last_signature:
                    last_signature = signature
                    yield f"data: {json.dumps({'logs': logs}, ensure_ascii=False)}\n\n"
                else:
                    yield ": keepalive\n\n"
            except (EngineError, asyncio.CancelledError):
                return
            await asyncio.sleep(2.0)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/datasets/{dataset_id}/documents")
async def knowledge_delete_documents(dataset_id: str, request: Request) -> Any:
    _require_enabled()
    _require_same_origin(request)
    body = await _json_body(request)
    ids = [str(x) for x in ((body or {}).get("ids") or [])]
    return await _engine_call(lambda e: e.delete_documents(dataset_id, ids), dataset_id=dataset_id)


@router.post("/datasets/{dataset_id}/documents/parse")
async def knowledge_parse_documents(dataset_id: str, request: Request) -> Any:
    _require_enabled()
    _require_same_origin(request)
    body = await _json_body(request)
    ids = [str(x) for x in ((body or {}).get("document_ids") or [])]
    return await _engine_call(lambda e: e.parse_documents(dataset_id, ids), dataset_id=dataset_id)


@router.post("/datasets/{dataset_id}/documents/stop")
async def knowledge_stop_documents(dataset_id: str, request: Request) -> Any:
    _require_enabled()
    _require_same_origin(request)
    body = await _json_body(request)
    ids = [str(x) for x in ((body or {}).get("document_ids") or [])]
    return await _engine_call(lambda e: e.stop_parsing(dataset_id, ids), dataset_id=dataset_id)


@router.get("/datasets/{dataset_id}/documents/{document_id}", response_model=KnowledgeDocument)
async def knowledge_get_document(dataset_id: str, document_id: str) -> Any:
    _require_enabled()
    return await _engine_call(
        lambda e: e.get_document(dataset_id, document_id), dataset_id=dataset_id
    )


# ---------------------------------------------------------------------------
# 摄取记录 / 检索试玩 / 预览
# ---------------------------------------------------------------------------


@router.get("/datasets/{dataset_id}/ingestions", response_model=list[IngestionLog])
async def knowledge_list_ingestions(dataset_id: str, request: Request) -> Any:
    _require_enabled()
    q = request.query_params
    return await _engine_call(
        lambda e: e.list_ingestions(
            dataset_id,
            log_type=str(q.get("log_type") or "file"),
            page=_int_param(q, "page", 1),
            page_size=_int_param(q, "page_size", 5),
        ),
        dataset_id=dataset_id,
    )


@router.get("/datasets/{dataset_id}/ingestions/{log_id}", response_model=IngestionLog)
async def knowledge_get_ingestion(dataset_id: str, log_id: str) -> Any:
    _require_enabled()
    return await _engine_call(lambda e: e.get_ingestion(dataset_id, log_id), dataset_id=dataset_id)


@router.post("/datasets/{dataset_id}/search", response_model=SearchResult)
async def knowledge_search_dataset(dataset_id: str, request: Request) -> Any:
    _require_enabled()
    body = await _json_body(request)
    payload = body if isinstance(body, dict) else {}
    return await _engine_call(
        lambda e: e.search(
            dataset_id,
            str(payload.get("question") or ""),
            top_k=int(payload.get("top_k") or 1024),
            similarity_threshold=float(payload.get("similarity_threshold") or 0.2),
            vector_similarity_weight=float(payload.get("vector_similarity_weight") or 0.3),
            page=int(payload.get("page") or 1),
            size=int(payload.get("size") or 30),
        ),
        dataset_id=dataset_id,
    )


@router.get("/datasets/{dataset_id}/documents/{document_id}/chunks", response_model=ChunkPage)
async def knowledge_list_chunks(dataset_id: str, document_id: str, request: Request) -> Any:
    """文档内分块列表（P2-T9；上游 data.chunks + data.total）。"""
    _require_enabled()
    q = request.query_params
    return await _engine_call(
        lambda e: e.list_chunks(
            dataset_id,
            document_id,
            page=_int_param(q, "page", 1),
            page_size=_int_param(q, "page_size", 20),
        ),
        dataset_id=dataset_id,
    )


@router.put(
    "/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}",
    response_model=MutationResult,
)
async def knowledge_update_chunk(
    dataset_id: str, document_id: str, chunk_id: str, request: Request
) -> Any:
    """分块编辑（content/available/important_keywords 可选；available→available_int）。"""
    _require_enabled()
    _require_same_origin(request)
    body = await _json_body(request)
    payload = body if isinstance(body, dict) else {}
    content = payload.get("content")
    available = payload.get("available")
    keywords = payload.get("important_keywords")
    return await _engine_call(
        lambda e: e.update_chunk(
            dataset_id,
            document_id,
            chunk_id,
            content=None if content is None else str(content),
            available=None if available is None else bool(available),
            important_keywords=[str(k) for k in keywords if isinstance(k, (str, int))]
            if isinstance(keywords, list)
            else None,
        ),
        dataset_id=dataset_id,
    )


@router.delete(
    "/datasets/{dataset_id}/documents/{document_id}/chunks", response_model=MutationResult
)
async def knowledge_delete_chunks(dataset_id: str, document_id: str, request: Request) -> Any:
    """批量删除分块（上游为集合端点 + chunk_ids body，item DELETE 405——见探针）。"""
    _require_enabled()
    _require_same_origin(request)
    body = await _json_body(request)
    payload = body if isinstance(body, dict) else {}
    ids = payload.get("chunk_ids")
    chunk_ids = [str(x) for x in ids if str(x).strip()] if isinstance(ids, list) else []
    if not chunk_ids:
        raise HTTPException(status_code=400, detail="chunk_ids is required.")
    return await _engine_call(
        lambda e: e.delete_chunks(dataset_id, document_id, chunk_ids),
        dataset_id=dataset_id,
    )


@router.get("/engines")
async def knowledge_engine_catalog() -> Any:
    """引擎目录（P3 T12 12.1）：kb_count 为 best-effort（R6：单引擎失败置 null）。"""
    _require_enabled()
    from openkg_webui.services.knowledge.engines import build_context, build_engine
    from openkg_webui.services.knowledge.engines.registry import (
        engine_detail_path_template,
        engine_display_name,
        iter_engine_ids,
    )

    entries: list[dict[str, Any]] = []
    for engine_id in iter_engine_ids():
        entry: dict[str, Any] = {
            "engine_id": engine_id,
            "display_name": engine_display_name(engine_id),
            "capabilities": [],
            "configured": True,
            "is_default": engine_id == DEFAULT_ENGINE_ID,
            "kb_count": None,
            "detail_path_template": engine_detail_path_template(engine_id),
            "error": "",
        }
        try:
            ctx = build_context(user_id=_user_id())
            engine = build_engine(engine_id, ctx=ctx, transport=_transport)
            entry["capabilities"] = sorted(getattr(engine, "capabilities", []) or [])
            page = await engine.list_datasets(page=1, page_size=1)
            entry["kb_count"] = int(page.total)
        except Exception as exc:  # noqa: BLE001 — R6：单引擎失败不拖垮目录
            entry["error"] = str(exc)
        entries.append(entry)
    return entries


@router.get("/models")
async def knowledge_embedding_models() -> Any:
    """嵌入模型候选清单（P3 T13' 13.0）——Settings 更换流程下拉的数据源。"""
    _require_enabled()
    return await _engine_call(lambda e: e.list_embedding_models())


@router.post("/datasets/{dataset_id}/embedding/check")
async def knowledge_check_embedding(dataset_id: str, request: Request) -> Any:
    """嵌入兼容性检查（P3 T13' 13.1）：只读探针，结论含 compatible 判定。"""
    _require_enabled()
    _require_same_origin(request)
    body = await _json_body(request)
    payload = body if isinstance(body, dict) else {}
    embd_id = str(payload.get("embd_id") or "").strip()
    if not embd_id:
        raise HTTPException(status_code=400, detail="embd_id is required.")
    try:
        # 上限 20：check_num 直接决定上游重嵌入算力消耗（评审安全 S2）
        check_num = max(1, min(int(payload.get("check_num") or 5), 20))
    except (TypeError, ValueError):
        check_num = 5
    return await _engine_call(
        lambda e: e.check_embedding_compatibility(dataset_id, embd_id, check_num=check_num),
        dataset_id=dataset_id,
    )


@router.get("/datasets/{dataset_id}/graph", response_model=KnowledgeGraph)
async def knowledge_graph(dataset_id: str) -> Any:
    """知识图谱载荷（未构建时 nodes/edges 为空数组）。"""
    _require_enabled()
    return await _engine_call(lambda e: e.get_knowledge_graph(dataset_id), dataset_id=dataset_id)


@router.get("/datasets/{dataset_id}/index")
async def knowledge_index_status(dataset_id: str, request: Request) -> Any:
    """graph/raptor 索引任务状态——上游原样对象。未构建必须序列化为 `{}`：
    包一层 `{"raw": …}` 会让前端"非空键即构建中"的判定误报。"""
    _require_enabled()
    index_type = str(request.query_params.get("type") or "graph").lower()

    async def _status(engine: Any) -> dict[str, Any]:
        status = await engine.get_index_status(dataset_id, index_type)
        return dict(getattr(status, "raw", {}) or {})

    return await _engine_call(_status, dataset_id=dataset_id)


@router.post("/datasets/{dataset_id}/index", response_model=MutationResult)
async def knowledge_build_index(dataset_id: str, request: Request) -> Any:
    """显式触发 graph/raptor 索引构建（成本高，仅用户动作；不做自动触发）。"""
    _require_enabled()
    _require_same_origin(request)
    index_type = str(request.query_params.get("type") or "graph").lower()
    return await _engine_call(
        lambda e: e.build_index(dataset_id, index_type), dataset_id=dataset_id
    )


@router.delete("/datasets/{dataset_id}/index", response_model=MutationResult)
async def knowledge_delete_index(dataset_id: str, request: Request) -> Any:
    _require_enabled()
    _require_same_origin(request)
    index_type = str(request.query_params.get("type") or "graph").lower()
    return await _engine_call(
        lambda e: e.delete_index(dataset_id, index_type), dataset_id=dataset_id
    )


@router.get("/documents/{document_id}/preview")
async def knowledge_preview_document(document_id: str) -> Response:
    _require_enabled()
    payload = await _engine_call(lambda e: e.preview_document(document_id))
    return Response(content=payload.content, media_type=payload.media_type)


@router.get("/thumbnails")
async def knowledge_thumbnail(request: Request) -> Response:
    _require_enabled()
    doc_id = str(request.query_params.get("doc_id") or "")
    payload = await _engine_call(lambda e: e.thumbnail(doc_id))
    return Response(content=payload.content, media_type=payload.media_type)
