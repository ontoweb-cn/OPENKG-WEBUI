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

import { requestJson } from "@/shared/api/client";
import { ApiError } from "@/shared/api/errors";

import {
  parseIngestionLogs,
  parseKnowledgeDatasets,
  parseKnowledgeDocuments,
  parseSearchChunks,
  type KnowledgeDataset,
  type KnowledgeDocument,
  type KnowledgeIngestionLog,
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

// —— 文档 ——

export async function fetchDocuments(
  datasetId: string,
  signal?: AbortSignal,
): Promise<{ documents: KnowledgeDocument[]; total: number }> {
  const data = await requestKnowledge<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents?page=1&page_size=100`,
    { cache: "no-store", signal },
  );
  return parseKnowledgeDocuments(data);
}

export async function uploadDocuments(
  datasetId: string,
  files: File[],
): Promise<void> {
  const form = new FormData();
  // 上游 document_api 读 files.getlist("file")——字段名单数（评审 R-1）
  for (const file of files) form.append("file", file, file.name);
  form.append("type", "local");
  await requestJson<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents`,
    { method: "POST", body: form, scope: "knowledge" },
  );
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

export async function searchDataset(
  datasetId: string,
  question: string,
  signal?: AbortSignal,
): Promise<KnowledgeSearchChunk[]> {
  const payload = await requestJson<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/search`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        page: 1,
        size: 10,
        similarity_threshold: 0.2,
      }),
      signal,
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
