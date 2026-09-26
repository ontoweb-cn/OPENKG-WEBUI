/**
 * 知识中心 transport（docs/knowledge-center-port-design.md §三；Phase 1a T5）。
 *
 * 一律走 requestJson/apiFetch（cookie 鉴权、401 跳转、ApiError 归一），错误
 * scope 归入 "knowledge"；URL 保持 app 相对路径（/api/knowledge-center 代理），
 * 由 apiFetch 层统一处理子路径部署。
 *
 * Phase 3 T3：后端出站为**域形状**（信封在 provider 内解包、错误映射为 HTTP
 * 状态码），因此本层不再有 unwrapEnvelope / KnowledgeApiError——上游错误直接
 * 表现为 ApiError（status + detail）。上传用 FormData（apiFetch 不覆写
 * Content-Type，fetch 自动带 boundary）。
 */

import { apiUrl, requestJson } from "@/shared/api/client";
import { ApiError } from "@/shared/api/errors";

import {
  parseEmbeddingCheckResult,
  parseEmbeddingModelOptions,
  parseIngestionLogs,
  parseKnowledgeChunks,
  parseKnowledgeDatasets,
  parseKnowledgeDocuments,
  parseKnowledgeDocument,
  parseKnowledgeGraph,
  parseKnowledgePreferences,
  parseSearchChunks,
  type EmbeddingCheckResult,
  type EmbeddingModelOption,
  type KnowledgeChunk,
  type KnowledgeDataset,
  type KnowledgeDocument,
  type KnowledgeGraphData,
  type KnowledgeIngestionLog,
  type KnowledgePreferences,
  type KnowledgeSearchChunk,
} from "./model";

/** 统一带上 knowledge 错误 scope 的薄封装（T3：不再解信封）。 */
async function requestKnowledge<T>(
  path: string,
  init: Parameters<typeof requestJson>[1] = {},
): Promise<T> {
  return requestJson<T>(path, { ...init, scope: "knowledge" });
}

// —— 状态 ——

export interface KnowledgeStatus {
  enabled: boolean;
  identity_ok: boolean | null;
  /**
   * 新建知识库**实际**会得到的可见范围。上游按 Team/Project 头决定 visibility
   * 并忽略请求体里的 permission，所以创建时无可选项——UI 应陈述该值。
   */
  create_visibility?: "private" | "team" | "project";
}

export function fetchKnowledgeStatus(): Promise<KnowledgeStatus> {
  return requestJson<KnowledgeStatus>("/api/knowledge-center", {
    cache: "no-store",
    scope: "knowledge",
  });
}

// —— 知识库 ——

export async function fetchDatasets(signal?: AbortSignal): Promise<KnowledgeDataset[]> {
  const data = await requestKnowledge<unknown>("/api/knowledge-center/datasets", {
    cache: "no-store",
    signal,
  });
  return parseKnowledgeDatasets(data);
}

export async function createDataset(body: {
  name: string;
  description?: string;
  permission?: "me" | "team";
}): Promise<KnowledgeDataset | null> {
  const data = await requestKnowledge<unknown>("/api/knowledge-center/datasets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const row = data && typeof data === "object" ? (data as Record<string, unknown>) : {};
  // 上游 create 返回单行（可能包 collection 形状），宽松取 id/name
  const inner = (row.dataset ?? row) as Record<string, unknown>;
  return parseKnowledgeDatasets({ datasets: [inner] })[0] ?? null;
}

export async function deleteDataset(datasetId: string): Promise<void> {
  await requestKnowledge<void>(`/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}`, {
    method: "DELETE",
  });
}

export async function fetchDataset(datasetId: string): Promise<KnowledgeDataset | null> {
  const data = await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}`,
    { cache: "no-store" },
  );
  const row = data && typeof data === "object" ? (data as Record<string, unknown>) : {};
  const inner = (row.dataset ?? row) as Record<string, unknown>;
  return parseKnowledgeDatasets({ datasets: [inner] })[0] ?? null;
}

/** P1-T8：部分更新知识库（name/description/embeddingModel 可选），响应为回读后的服务端状态。
 *  embeddingModel 走服务端 D5 强制检查（不兼容返回 409）。 */
export async function updateDataset(
  datasetId: string,
  patch: { name?: string; description?: string; embeddingModel?: string },
): Promise<KnowledgeDataset> {
  const body: Record<string, string> = {};
  if (patch.name != null) body.name = patch.name;
  if (patch.description != null) body.description = patch.description;
  if (patch.embeddingModel != null) body.embedding_model = patch.embeddingModel;
  const data = await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
  );
  const row = data && typeof data === "object" ? (data as Record<string, unknown>) : {};
  const inner = (row.dataset ?? row) as Record<string, unknown>;
  return parseKnowledgeDatasets({ datasets: [inner] })[0] as KnowledgeDataset;
}

// —— 文档 ——

// —— 文档（P1-T5：服务端分页接通）——

export async function fetchDocuments(
  datasetId: string,
  options: { page?: number; pageSize?: number; signal?: AbortSignal } = {},
): Promise<{ documents: KnowledgeDocument[]; total: number }> {
  const page = Math.max(1, options.page ?? 1);
  const pageSize = Math.max(1, options.pageSize ?? 50);
  const payload = await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents?page=${page}&page_size=${pageSize}`,
    { cache: "no-store", signal: options.signal },
  );
  return parseKnowledgeDocuments(payload);
}

/**
 * 单文件上传（XHR：`upload.onprogress` 提供浏览器→代理段的字节进度——
 * 大文件的主要耗时段。代理→上游转发无事件，100% 后为"服务端处理中"。
 * XHR 不走 requestJson 的 401 重定向（上传中断开登录属边缘场景，错误原样抛出）。
 */
export function uploadDocumentWithProgress(
  datasetId: string,
  file: File,
  onProgress: (percent: number) => void,
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const form = new FormData();
    // 上游 document_api 读 files.getlist("file")——字段名单数（评审 R-1）
    form.append("file", file, file.name);
    form.append("type", "local");
    const xhr = new XMLHttpRequest();
    xhr.open(
      "POST",
      apiUrl(`/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents`),
    );
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.min(99, Math.round((event.loaded / event.total) * 100)));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(100);
        resolve();
        return;
      }
      let detail = `HTTP ${xhr.status}`;
      try {
        const parsed = JSON.parse(xhr.responseText) as { detail?: unknown };
        if (typeof parsed.detail === "string" && parsed.detail) detail = parsed.detail;
      } catch {
        /* 非 JSON 错误体，保留状态码 */
      }
      reject(new Error(detail));
    };
    xhr.onerror = () => reject(new Error("network error"));
    xhr.send(form);
  });
}

export async function deleteDocuments(
  datasetId: string,
  documentIds: string[],
): Promise<void> {
  await requestKnowledge<void>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents`,
    { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: documentIds }) },
  );
}

export async function parseDocuments(
  datasetId: string,
  documentIds: string[],
): Promise<void> {
  await requestKnowledge<void>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents/parse`,
    // 上游 parse/stop 的 body 键是 document_ids（评审 R-2）
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ document_ids: documentIds }) },
  );
}

export async function stopParsing(
  datasetId: string,
  documentIds: string[],
): Promise<void> {
  await requestKnowledge<void>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents/stop`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ document_ids: documentIds }) },
  );
}

// —— 嵌入模型兼容性检查（P3 T13'）——

export async function fetchEmbeddingModels(): Promise<EmbeddingModelOption[]> {
  const payload = await requestKnowledge<unknown>("/api/knowledge-center/models", {
    cache: "no-store",
  });
  return parseEmbeddingModelOptions(payload);
}

export async function checkEmbeddingCompatibility(
  datasetId: string,
  embdId: string,
): Promise<EmbeddingCheckResult> {
  const payload = await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/embedding/check`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ embd_id: embdId }),
      scope: "knowledge",
    },
  );
  return parseEmbeddingCheckResult(payload);
}

// —— 文档预览（P0-T2）——

/**
 * 单文档获取（P1 收尾 T-C：`?file=` 深链的兜底解析——文档不在当前页时仍可
 * 打开预览。端点已存在：knowledge.py 单文档 GET）。
 */
export async function getDocument(
  datasetId: string,
  documentId: string,
): Promise<KnowledgeDocument | null> {
  const payload = await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents/${encodeURIComponent(documentId)}`,
    { cache: "no-store" },
  );
  const row = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const inner = (row.document ?? row) as Record<string, unknown>;
  const doc = parseKnowledgeDocument(inner);
  return doc.id ? doc : null;
}

/**
 * 文档原始字节流地址（app 相对路径；直接取用时经 apiUrl() 套部署前缀）。
 * 上游按委托用户做 per-user 可见性强制
 * （scripts/knowledge_a0/results/t0_preview_auth_audit.md）。
 */
export function previewUrl(documentId: string): string {
  return `/api/knowledge-center/documents/${encodeURIComponent(documentId)}/preview`;
}

// —— per-user 偏好（默认知识库，P0-T4）——

export async function fetchKnowledgePreferences(): Promise<KnowledgePreferences> {
  const payload = await requestKnowledge<unknown>("/api/knowledge-center/preferences", {
    cache: "no-store",
  });
  return parseKnowledgePreferences(payload);
}

export async function putKnowledgePreferences(
  defaultDatasetId: string | null,
): Promise<KnowledgePreferences> {
  const payload = await requestKnowledge<unknown>("/api/knowledge-center/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ default_dataset_id: defaultDatasetId ?? "" }),
    scope: "knowledge",
  });
  return parseKnowledgePreferences(payload);
}

// —— chunk 管理（P2-T9）——

export async function listChunks(
  datasetId: string,
  documentId: string,
  options: { page?: number; pageSize?: number; signal?: AbortSignal } = {},
): Promise<{ chunks: KnowledgeChunk[]; total: number }> {
  const page = Math.max(1, options.page ?? 1);
  const pageSize = Math.max(1, options.pageSize ?? 20);
  const payload = await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents/${encodeURIComponent(documentId)}/chunks?page=${page}&page_size=${pageSize}&_t=${Date.now()}`,
    { cache: "no-store", signal: options.signal },
  );
  return parseKnowledgeChunks(payload);
}

export async function updateChunk(
  datasetId: string,
  documentId: string,
  chunkId: string,
  patch: { content?: string; available?: boolean; importantKeywords?: string[] },
): Promise<void> {
  const body: Record<string, unknown> = {};
  if (patch.content != null) body.content = patch.content;
  if (patch.available != null) body.available = patch.available;
  if (patch.importantKeywords != null) body.important_keywords = patch.importantKeywords;
  await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents/${encodeURIComponent(documentId)}/chunks/${encodeURIComponent(chunkId)}`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
  );
}

export async function deleteChunks(
  datasetId: string,
  documentId: string,
  chunkIds: string[],
): Promise<void> {
  await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents/${encodeURIComponent(documentId)}/chunks`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chunk_ids: chunkIds }),
    },
  );
}

// —— 知识图谱 / 索引构建（P2-T10）——

export async function fetchKnowledgeGraph(datasetId: string): Promise<KnowledgeGraphData> {
  const payload = await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/graph`,
    { cache: "no-store" },
  );
  return parseKnowledgeGraph(payload);
}

/** 索引任务状态：raw 为上游原样对象，空对象 = 未构建。 */
export async function fetchIndexStatus(
  datasetId: string,
  indexType: "graph" | "raptor",
): Promise<Record<string, unknown>> {
  const payload = await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/index?type=${indexType}`,
    { cache: "no-store" },
  );
  return payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
}

export async function buildIndex(
  datasetId: string,
  indexType: "graph" | "raptor",
): Promise<void> {
  await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/index?type=${indexType}`,
    { method: "POST" },
  );
}

export async function deleteIndex(
  datasetId: string,
  indexType: "graph" | "raptor",
): Promise<void> {
  await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/index?type=${indexType}`,
    { method: "DELETE" },
  );
}

// —— 摄取记录（进度日志） ——

export async function fetchIngestionLogs(
  datasetId: string,
  signal?: AbortSignal,
): Promise<KnowledgeIngestionLog[]> {
  const payload = await requestJson<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/ingestions?log_type=file&page=1&page_size=20`,
    { cache: "no-store", signal, scope: "knowledge" },
  );
  return parseIngestionLogs(payload);
}

// —— 检索试玩 ——

/** 检索试玩参数（P1-T7/R5：与引擎签名一一对应，缺省=引擎缺省口径）。 */
export interface SearchOptions {
  similarityThreshold?: number;
  topK?: number;
  vectorSimilarityWeight?: number;
}

export async function searchDataset(
  datasetId: string,
  question: string,
  options: SearchOptions & { signal?: AbortSignal } = {},
): Promise<KnowledgeSearchChunk[]> {
  const payload = await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/search`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        page: 1,
        size: 10,
        similarity_threshold: options.similarityThreshold ?? 0.2,
        top_k: options.topK ?? 1024,
        vector_similarity_weight: options.vectorSimilarityWeight ?? 0.3,
      }),
      signal: options.signal,
      scope: "knowledge",
    },
  );
  return parseSearchChunks(payload);
}

// —— Phase 2：结构化上传 / SSE 日志流 / GitHub 源 ——

export interface StructuredEntry {
  file: File;
  /** 相对路径（文件夹上传语义）；zip 文件留空由代理解包 */
  relPath?: string;
}

/** 结构化上传：zip 由代理解包，rel_paths 保留目录结构（D1/D2）。 */
export async function uploadStructured(
  datasetId: string,
  entries: StructuredEntry[],
): Promise<void> {
  const form = new FormData();
  const relPaths: string[] = [];
  for (const entry of entries) {
    form.append("file", entry.file, entry.file.name);
    relPaths.push(entry.relPath ?? entry.file.name);
  }
  form.append("rel_paths", JSON.stringify(relPaths));
  form.append("type", "local");
  await requestJson<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents/structured`,
    { method: "POST", body: form, scope: "knowledge" },
  );
}

/** 解析日志 SSE 流地址（EventSource 同源带 cookie）。 */
export function logsStreamUrl(datasetId: string): string {
  return `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/logs/stream`;
}

export interface GithubSource {
  repo: string;
  branch: string;
  path_prefix: string;
  glob: string;
  token_set: boolean;
  state: Record<string, unknown>;
}

export async function fetchGithubSource(
  datasetId: string,
): Promise<GithubSource | null> {
  try {
    const payload = await requestJson<GithubSource>(
      `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/sources/github`,
      { cache: "no-store", scope: "knowledge" },
    );
    return payload ?? null;
  } catch (error) {
    // T3：404 由后端映射为 HTTP 状态（不再靠信封 code）
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function saveGithubSource(
  datasetId: string,
  body: Record<string, unknown>,
): Promise<GithubSource> {
  const payload = await requestJson<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/sources/github`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), scope: "knowledge" },
  );
  return payload as GithubSource;
}

export async function deleteGithubSource(datasetId: string): Promise<void> {
  await requestJson<void>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/sources/github`,
    { method: "DELETE", scope: "knowledge" },
  );
}

export async function syncGithubSource(datasetId: string): Promise<{ started: boolean }> {
  const payload = await requestJson<{ started: boolean }>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/sources/github/sync`,
    { method: "POST", scope: "knowledge" },
  );
  return payload as { started: boolean };
}

// —— Phase 2 T5：Web 爬取源 ——

export interface WebSource {
  type: string;
  base_url: string;
  max_pages: number;
  max_depth: number;
  state: Record<string, unknown>;
}

export async function fetchWebSource(datasetId: string): Promise<WebSource | null> {
  try {
    const payload = await requestJson<WebSource>(
      `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/sources/web`,
      { cache: "no-store", scope: "knowledge" },
    );
    return payload ?? null;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function saveWebSource(
  datasetId: string,
  body: { base_url: string; max_pages?: number; max_depth?: number },
): Promise<WebSource> {
  const payload = await requestJson<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/sources/web`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), scope: "knowledge" },
  );
  return payload as WebSource;
}

export async function deleteWebSource(datasetId: string): Promise<void> {
  await requestJson<void>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/sources/web`,
    { method: "DELETE", scope: "knowledge" },
  );
}

export async function syncWebSource(datasetId: string): Promise<{ started: boolean }> {
  const payload = await requestJson<{ started: boolean }>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/sources/web/sync`,
    { method: "POST", scope: "knowledge" },
  );
  return payload as { started: boolean };
}
