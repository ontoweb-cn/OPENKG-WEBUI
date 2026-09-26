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
    classify_upstream,
)
from .models import (
    BinaryPayload,
    ChunkPage,
    DatasetPage,
    DocumentPage,
    GraphIndexStatus,
    IngestionLog,
    KnowledgeChunk,
    KnowledgeDataset,
    KnowledgeDocument,
    KnowledgeGraph,
    MutationResult,
    SearchChunk,
    SearchResult,
    UploadResult,
)

#: 管理面流量：连接 60s / 读流 300s（大文档 preview）。
UPSTREAM_TIMEOUT = httpx.Timeout(60.0, read=300.0)

#: MCP server 条目名与工具名（chat 会话的 ``.mcp.json`` / 权限放行使用）。
MCP_SERVER_NAME = "intellect-knowledge"
MCP_TOOL_NAME = "intellect_retrieval"

#: 上游 visibility 的合法取值（访问控制依据）。
_VISIBILITIES = frozenset({"private", "tenant", "team", "project"})


def _dataset_visibility(row: dict[str, Any]) -> str:
    """归一上游可见范围（评审 D5）。

    优先 ``visibility``（权威列，与 ``can_access_resource`` 同源）。

    缺失时从 legacy ``permission`` 推导，且必须**先看归属 id**，否则会把
    比实际更宽的范围报给用户（评审 P2-3）：
      - 有 ``team_id`` → ``"team"``；有 ``project_id`` → ``"project"``
        （上游的实际分支就是这两个字段，不看 permission）；
      - 只有 ``permission=="team"`` 而无归属 id → ``"tenant"``：上游把这类
        legacy 行按租户可见处理（无 team 分支可命中，租户成员可见）；
      - 其余（``permission=="me"``/缺失）→ ``"private"``。

    注：上游对 ``visibility`` 为 NULL/空的行按 ``"tenant"`` 兜底
    （``can_access_resource`` 的 ``_get("visibility", "tenant")``），而这里
    返回 ``"private"``——徽标宁可少承诺（"仅自己可见"实为租户可见）也不
    反向误导，且这类行在本部署为 0。
    """
    value = str(row.get("visibility") or "").strip().lower()
    if value in _VISIBILITIES:
        return value
    if str(row.get("team_id") or "").strip():
        return "team"
    if str(row.get("project_id") or "").strip():
        return "project"
    if str(row.get("permission") or "").strip().lower() == "team":
        return "tenant"
    return "private"


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

    # -- 助手：信封解包与域转换 -------------------------------------------

    def _raise_for_status(self, resp: httpx.Response, *, allow_404: bool = False) -> None:
        """HTTP 层错误归一（信封优先于 status，沿 T2 语义）。"""
        if resp.status_code < 400:
            return
        if allow_404 and resp.status_code == 404:
            raise EngineError(
                EngineErrorKind.NOT_FOUND,
                "resource not found",
                upstream_status=404,
            )
        if resp.status_code in (401, 403):
            raise EngineError(
                EngineErrorKind.UNAUTHORIZED,
                f"upstream rejected the request (HTTP {resp.status_code})",
                upstream_status=resp.status_code,
            )
        if resp.status_code == 404:
            raise EngineError(
                EngineErrorKind.NOT_FOUND,
                "resource not found",
                upstream_status=404,
            )
        if resp.status_code < 500:
            raise EngineError(
                EngineErrorKind.INVALID,
                f"upstream rejected the request (HTTP {resp.status_code})",
                upstream_status=resp.status_code,
            )
        raise EngineError(
            EngineErrorKind.UPSTREAM_ERROR,
            f"upstream failure (HTTP {resp.status_code})",
            upstream_status=resp.status_code,
        )

    def _unwrap(self, resp: httpx.Response, *, allow_404: bool = False) -> Any:
        """解包 ``{code, data, message}`` 信封 → ``data``；错误转 :class:`EngineError`。

        **信封优先**：HTTP 200 携带 ``code≠0`` 时按 code 分类（现状语义）；
        HTTP 层异常且信封缺失时按 status（评审 P4 的优先级）。
        """
        self._raise_for_status(resp, allow_404=allow_404)
        try:
            body = resp.json()
        except Exception:
            raise EngineError(
                EngineErrorKind.UPSTREAM_ERROR,
                "upstream returned a non-JSON body",
                upstream_status=resp.status_code,
            ) from None
        if not isinstance(body, dict) or "code" not in body:
            # 无信封（少数端点）：直接返回解析后的 body
            return body
        code = body.get("code")
        message = str(body.get("message") or "")
        kind = classify_upstream(code if isinstance(code, int) else None, message)
        if kind is not None:
            raise EngineError(kind, message or f"upstream error (code {code})", upstream_code=code)
        return body.get("data")

    @staticmethod
    def _dataset(raw: Any) -> KnowledgeDataset:
        row = raw if isinstance(raw, dict) else {}
        return KnowledgeDataset(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or ""),
            description=str(row.get("description") or ""),
            permission=str(row.get("permission") or "me"),
            # 可见范围以 visibility 为准；缺失时回退 permission（"team" 是
            # 旧行的租户级语义——上游把无 team_id 的 legacy team 行当
            # tenant 可见），再回退 private。
            visibility=_dataset_visibility(row),
            document_count=int(row.get("document_count") or 0),
            chunk_count=int(row.get("chunk_count") or 0),
            # 上游列名是 token_num（token_count 恒缺——收尾批次 R2 定性为映射 bug）
            token_count=int(row.get("token_num") or 0),
            embedding_model=str(row.get("embedding_model") or ""),
            created_at=str(row.get("create_time") or ""),
        )

    @staticmethod
    def _document(raw: Any) -> KnowledgeDocument:
        row = raw if isinstance(raw, dict) else {}
        run = str(row.get("run") or "").upper()
        if run not in ("UNSTART", "RUNNING", "DONE", "FAIL", "CANCEL"):
            run = "UNSTART"
        try:
            progress = float(row.get("progress") or 0.0)
        except (TypeError, ValueError):
            progress = 0.0
        return KnowledgeDocument(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or ""),
            run=run,  # type: ignore[arg-type]
            progress=max(0.0, min(100.0, progress)),
            chunk_count=int(row.get("chunk_count") or 0),
            token_count=int(row.get("token_count") or 0),
            size=int(row.get("size") or 0),
            location=str(row.get("location") or row.get("name") or ""),
        )

    async def health(self) -> bool:
        """轻量探测：``GET /datasets`` 是否可达（不抛错，返回布尔）。"""
        try:
            await self.list_datasets(page=1, page_size=1)
            return True
        except Exception:
            return False

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

    # 注：T2 时期此处另有一个返回 ``httpx.Response`` 的 ``upload``（原样透传
    # 信封给路由）。T3 域化后它被下方返回 ``UploadResult`` 的实现取代，但当时
    # 未删——同名方法在类体里后者覆盖前者，旧实现成为不可达死代码，且触发
    # ruff F811。已删除（2026-09-26）。

    # -- 管理面：类型化方法（T3；路径知识收回 provider 内部）----------------

    async def list_datasets(self, *, page: int = 1, page_size: int = 30) -> DatasetPage:
        """知识库分页。上游 total 在**信封顶层** ``total_datasets``（评审 P2）。"""
        resp = await self.request(
            "GET",
            "/datasets",
            params={"page": page, "page_size": page_size},
        )
        self._raise_for_status(resp)
        try:
            body = resp.json()
        except Exception:
            raise EngineError(
                EngineErrorKind.UPSTREAM_ERROR, "upstream returned a non-JSON body"
            ) from None
        if isinstance(body, dict):
            code = body.get("code")
            message = str(body.get("message") or "")
            kind = classify_upstream(code if isinstance(code, int) else None, message)
            if kind is not None:
                raise EngineError(
                    kind, message or f"upstream error (code {code})", upstream_code=code
                )
            rows = body.get("data")
            total = body.get("total_datasets")
        else:
            rows, total = body, None
        datasets = [self._dataset(row) for row in (rows if isinstance(rows, list) else [])]
        try:
            total_int = int(total) if total is not None else len(datasets)
        except (TypeError, ValueError):
            total_int = len(datasets)
        return DatasetPage(datasets=datasets, total=total_int)

    async def get_dataset(self, dataset_id: str) -> KnowledgeDataset:
        resp = await self.request("GET", f"/datasets/{dataset_id}")
        return self._dataset(self._unwrap(resp))

    async def create_dataset(
        self,
        *,
        name: str,
        description: str = "",
        permission: str = "me",
    ) -> KnowledgeDataset:
        resp = await self.request(
            "POST",
            "/datasets",
            json={"name": name, "description": description, "permission": permission},
        )
        data = self._unwrap(resp)
        row = data.get("dataset") if isinstance(data, dict) and "dataset" in data else data
        return self._dataset(row)

    async def update_dataset(
        self,
        dataset_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> KnowledgeDataset:
        """部分更新知识库；随后回读（P1-T8/R3：以服务端状态为准，防归一化字段漂移）。

        上游 ``PUT /datasets/{id}`` 接受部分 body（name/description/parser_config），
        DeepMentor client.py:273 已验证同一端点语义。
        """
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if not body:
            return await self.get_dataset(dataset_id)
        # PUT 响应必须解包：上游对无权限/重名等返回非零 code 信封
        # （实测 code 102 "lacks permission"），吞掉会变成"假成功"。
        resp = await self.request("PUT", f"/datasets/{dataset_id}", json=body)
        self._unwrap(resp)
        return await self.get_dataset(dataset_id)

    async def delete_dataset(self, dataset_id: str) -> None:
        resp = await self.request("DELETE", f"/datasets/{dataset_id}")
        self._unwrap(resp)

    async def list_documents(
        self, dataset_id: str, *, page: int = 1, page_size: int = 100
    ) -> DocumentPage:
        """文档分页（上游 ``data.docs`` + ``data.total``）。"""
        resp = await self.request(
            "GET",
            f"/datasets/{dataset_id}/documents",
            params={"page": page, "page_size": page_size},
        )
        data = self._unwrap(resp)
        inner = data if isinstance(data, dict) else {}
        rows = inner.get("docs") if isinstance(inner.get("docs"), list) else []
        try:
            total = int(inner.get("total") or len(rows))
        except (TypeError, ValueError):
            total = len(rows)
        return DocumentPage(documents=[self._document(r) for r in rows], total=total)

    async def get_document(self, dataset_id: str, document_id: str) -> KnowledgeDocument:
        resp = await self.request("GET", f"/datasets/{dataset_id}/documents/{document_id}")
        return self._document(self._unwrap(resp))

    async def upload(
        self,
        dataset_id: str,
        items: list[UploadItem],
        *,
        parent_path: str = "",
        upload_type: str = "local",
    ) -> UploadResult:  # type: ignore[override]
        """上传一组文件；返回域形状结果。

        **流式契约（决策 D4）**：``UploadItem.content`` 为 ``bytes | BinaryIO``——
        直传路径传 file-like，httpx 分块读取（不 buffer）；结构化上传按设计传
        bytes（zip 解包必然如此）。
        """
        payload = [("file", (item.name, item.content, item.content_type)) for item in items]
        data: dict[str, Any] = {"type": upload_type}
        if parent_path:
            data["parent_path"] = parent_path
        resp = await self.request(
            "POST", f"/datasets/{dataset_id}/documents", files=payload, data=data
        )
        raw = self._unwrap(resp)
        rows = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
        documents = [self._document(r) for r in rows if isinstance(r, dict) and r.get("id")]
        return UploadResult(uploaded=len(documents), documents=documents)

    async def delete_documents(self, dataset_id: str, document_ids: list[str]) -> None:
        resp = await self.request(
            "DELETE",
            f"/datasets/{dataset_id}/documents",
            json={"ids": list(document_ids)},
        )
        self._unwrap(resp)

    async def parse_documents(self, dataset_id: str, document_ids: list[str]) -> None:
        resp = await self.request(
            "POST",
            f"/datasets/{dataset_id}/documents/parse",
            json={"document_ids": list(document_ids)},
        )
        self._unwrap(resp)

    async def stop_parsing(self, dataset_id: str, document_ids: list[str]) -> None:
        resp = await self.request(
            "POST",
            f"/datasets/{dataset_id}/documents/stop",
            json={"document_ids": list(document_ids)},
        )
        self._unwrap(resp)

    async def search(
        self,
        dataset_id: str,
        question: str,
        *,
        top_k: int = 1024,
        similarity_threshold: float = 0.2,
        vector_similarity_weight: float = 0.3,
        page: int = 1,
        size: int = 30,
        **options: Any,
    ) -> SearchResult:
        """单库检索（含上游数据级权限信号 ``denied_dataset_ids``）。"""
        body: dict[str, Any] = {
            "question": question,
            "page": page,
            "size": size,
            "top_k": top_k,
            "similarity_threshold": similarity_threshold,
            "vector_similarity_weight": vector_similarity_weight,
            **options,
        }
        resp = await self.request("POST", f"/datasets/{dataset_id}/search", json=body)
        data = self._unwrap(resp)
        inner = data if isinstance(data, dict) else {}
        rows = inner.get("chunks") if isinstance(inner.get("chunks"), list) else []
        chunks: list[SearchChunk] = []
        for raw in rows:
            row = raw if isinstance(raw, dict) else {}
            try:
                similarity = float(row.get("similarity") or 0.0)
            except (TypeError, ValueError):
                similarity = 0.0
            chunks.append(
                SearchChunk(
                    # 上游实际字段名（运行时验收 R-6）：chunk_id/content_with_weight/docnm_kwd
                    id=str(row.get("chunk_id") or row.get("id") or ""),
                    content=str(row.get("content_with_weight") or row.get("content") or ""),
                    similarity=similarity,
                    document_name=str(row.get("docnm_kwd") or row.get("document_name") or ""),
                )
            )
        denied = inner.get("denied_dataset_ids")
        try:
            total = int(inner.get("total") or len(chunks))
        except (TypeError, ValueError):
            total = len(chunks)
        return SearchResult(
            chunks=chunks,
            total=total,
            denied_dataset_ids=[str(x) for x in denied] if isinstance(denied, list) else [],
        )

    async def list_ingestions(
        self, dataset_id: str, *, log_type: str = "file", page: int = 1, page_size: int = 5
    ) -> list[IngestionLog]:
        resp = await self.request(
            "GET",
            f"/datasets/{dataset_id}/ingestions",
            params={"log_type": log_type, "page": page, "page_size": page_size},
        )
        data = self._unwrap(resp)
        inner = data if isinstance(data, dict) else {}
        rows = inner.get("logs") if isinstance(inner.get("logs"), list) else []
        return [self._ingestion_log(r) for r in rows]

    async def get_ingestion(self, dataset_id: str, log_id: str) -> IngestionLog:
        resp = await self.request("GET", f"/datasets/{dataset_id}/ingestions/{log_id}")
        return self._ingestion_log(self._unwrap(resp))

    @staticmethod
    def _ingestion_log(raw: Any) -> IngestionLog:
        row = raw if isinstance(raw, dict) else {}
        try:
            progress = float(row.get("progress") or 0.0)
        except (TypeError, ValueError):
            progress = 0.0
        return IngestionLog(
            id=str(row.get("id") or ""),
            progress=progress,
            message=str(row.get("progress_msg") or ""),
            status=str(row.get("operation_status") or ""),
            document_name=str(row.get("document_name") or ""),
        )

    async def preview_document(self, document_id: str) -> BinaryPayload:
        resp = await self.request("GET", f"/documents/{document_id}/preview")
        self._raise_for_status(resp)
        return BinaryPayload(
            content=resp.content,
            media_type=resp.headers.get("content-type", "application/octet-stream"),
        )

    async def thumbnail(self, document_id: str) -> BinaryPayload:
        resp = await self.request("GET", "/thumbnails", params={"doc_id": document_id})
        self._raise_for_status(resp)
        return BinaryPayload(
            content=resp.content,
            media_type=resp.headers.get("content-type", "application/octet-stream"),
        )

    # -- chunk 管理（P2-T9；变更语义见 scripts/knowledge_a0/results/t9p_chunk_mutation_probe.md）--

    async def list_chunks(
        self, dataset_id: str, document_id: str, *, page: int = 1, page_size: int = 20
    ) -> ChunkPage:
        resp = await self.request(
            "GET",
            f"/datasets/{dataset_id}/documents/{document_id}/chunks",
            params={"page": page, "page_size": page_size},
        )
        data = self._unwrap(resp)
        inner = data if isinstance(data, dict) else {}
        rows = inner.get("chunks") if isinstance(inner.get("chunks"), list) else []
        try:
            total = int(inner.get("total") or len(rows))
        except (TypeError, ValueError):
            total = len(rows)
        return ChunkPage(
            chunks=[self._chunk(r) for r in rows if isinstance(r, dict)],
            total=total,
        )

    async def update_chunk(
        self,
        dataset_id: str,
        document_id: str,
        chunk_id: str,
        *,
        content: str | None = None,
        available: bool | None = None,
        important_keywords: list[str] | None = None,
    ) -> MutationResult:
        body: dict[str, Any] = {}
        if content is not None:
            body["content"] = content
        if available is not None:
            body["available"] = available
        if important_keywords is not None:
            body["important_keywords"] = important_keywords
        if not body:
            return MutationResult(ok=True)
        resp = await self.request(
            "PATCH", f"/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}", json=body
        )
        self._unwrap(resp)
        return MutationResult(ok=True)

    async def delete_chunks(
        self, dataset_id: str, document_id: str, chunk_ids: list[str]
    ) -> MutationResult:
        # 上游删除在集合端点 + chunk_ids body（item DELETE 为 405，见探针）
        resp = await self.request(
            "DELETE",
            f"/datasets/{dataset_id}/documents/{document_id}/chunks",
            json={"chunk_ids": list(chunk_ids)},
        )
        self._unwrap(resp)
        return MutationResult(ok=True)

    @staticmethod
    def _chunk(raw: Any) -> KnowledgeChunk:
        row = raw if isinstance(raw, dict) else {}
        # available 键在 list 载荷中可为 null，以 available_int(0/1) 为权威（探针 R1）
        available = row.get("available")
        if available is None:
            available = row.get("available_int")
        keywords = row.get("important_keywords")
        return KnowledgeChunk(
            id=str(row.get("id") or ""),
            content=str(row.get("content") or ""),
            available=bool(available) if available is not None else True,
            important_keywords=[str(k) for k in keywords if isinstance(k, (str, int))]
            if isinstance(keywords, list)
            else [],
        )

    # -- 知识图谱 / 索引构建（P2-T10）--

    async def get_knowledge_graph(self, dataset_id: str) -> KnowledgeGraph:
        resp = await self.request("GET", f"/datasets/{dataset_id}/knowledge_graph")
        data = self._unwrap(resp)
        inner = data if isinstance(data, dict) else {}
        graph = inner.get("graph") if isinstance(inner.get("graph"), dict) else {}
        nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
        edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
        return KnowledgeGraph(
            nodes=[n for n in nodes if isinstance(n, dict)],
            edges=[e for e in edges if isinstance(e, dict)],
        )

    async def get_index_status(self, dataset_id: str, index_type: str) -> GraphIndexStatus:
        resp = await self.request(
            "GET", f"/datasets/{dataset_id}/index", params={"type": index_type}
        )
        data = self._unwrap(resp)
        return GraphIndexStatus(raw=data if isinstance(data, dict) else {})

    async def build_index(self, dataset_id: str, index_type: str) -> MutationResult:
        if index_type not in ("graph", "raptor"):
            raise ValueError(f"unsupported index type: {index_type}")
        resp = await self.request(
            "POST", f"/datasets/{dataset_id}/index", params={"type": index_type}
        )
        self._unwrap(resp)
        return MutationResult(ok=True)

    async def delete_index(self, dataset_id: str, index_type: str) -> MutationResult:
        if index_type not in ("graph", "raptor"):
            raise ValueError(f"unsupported index type: {index_type}")
        resp = await self.request(
            "DELETE", f"/datasets/{dataset_id}/index", params={"type": index_type}
        )
        self._unwrap(resp)
        return MutationResult(ok=True)

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
                **{k: v for k, v in identity_headers.items() if k.lower() != "authorization"},
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
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", "utf-8")
