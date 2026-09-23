# -*- coding: utf-8 -*-
"""知识中心域模型（Phase 3 T3）。

出站形状（路由层直接序列化为 HTTP 响应），**不携带上游信封与 RagFlow 专有
字段名**——引擎差异在 provider 内吸收（设计 §三；T3 方案 §二）。

字段命名保持 snake_case：前端 parse 层读取的即为该命名（`document_count` /
`chunk_count` / `run` / `progress`），沿用可使前端字段读取零改动（决策 D1）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

#: 上游解析状态机（RagFlow 派生口径）。
DocumentRun = Literal["UNSTART", "RUNNING", "DONE", "FAIL", "CANCEL"]


class KnowledgeDataset(BaseModel):
    """知识库（域模型；`engine_id` 由路由层按需附加，不属引擎出站形状）。"""

    id: str
    name: str
    description: str = ""
    permission: str = "me"  # "me" | "team"（可见范围）
    document_count: int = 0
    chunk_count: int = 0
    token_count: int = 0
    created_at: str = ""


class DatasetPage(BaseModel):
    """知识库分页。

    注（评审 P2）：上游 ``GET /datasets`` 的 total 位于**信封顶层**
    ``total_datasets``（``data`` 是裸列表）——与文档列表的 ``data.total``
    不同，provider 负责在各自端点取正确位置。
    """

    datasets: list[KnowledgeDataset] = []
    total: int = 0


class KnowledgeDocument(BaseModel):
    """文档（域模型；`run` 为归一后的解析状态）。"""

    id: str
    name: str
    run: DocumentRun = "UNSTART"
    progress: float = 0.0  # 0-100
    chunk_count: int = 0
    token_count: int = 0
    size: int = 0
    location: str = ""


class DocumentPage(BaseModel):
    """文档分页（上游 ``data.docs`` + ``data.total``）。"""

    documents: list[KnowledgeDocument] = []
    total: int = 0


class SearchChunk(BaseModel):
    """检索命中的分块。"""

    id: str
    content: str
    similarity: float = 0.0
    document_name: str = ""


class SearchResult(BaseModel):
    """检索结果（含上游的数据级权限信号 ``denied_dataset_ids``）。

    该信号当前前端未消费，纳入契约是为未来"勾选的库无权访问"的结构化提示
    留位置（1.5 评审 R4 曾因它只存在于检索文本而把验收降级为"由 agent 说明"）。
    """

    chunks: list[SearchChunk] = []
    total: int = 0
    denied_dataset_ids: list[str] = []


class IngestionLog(BaseModel):
    """摄取（解析）日志——进度与日志文本。

    ``message`` 对应上游 ``progress_msg``（多行累积文本）。
    """

    id: str
    progress: float = 0.0
    message: str = ""
    status: str = ""
    document_name: str = ""


class UploadResult(BaseModel):
    """上传结果（直传与结构化上传共用）。"""

    uploaded: int = 0
    documents: list[KnowledgeDocument] = []


class StructuredUploadResult(BaseModel):
    """结构化上传按目录分组的聚合结果（T3 前为 ``{directory, status, upstream}``）。"""

    directory: str
    uploaded: int = 0
    error: str = ""


class BinaryPayload(BaseModel):
    """二进制透传载体（文档预览/缩略图）——不进 JSON 契约。"""

    content: bytes
    media_type: str = "application/octet-stream"
