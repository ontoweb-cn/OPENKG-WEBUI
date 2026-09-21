/**
 * 知识中心 transport（docs/knowledge-center-port-design.md §三；Phase 1a T5）。
 *
 * 一律走 requestJson/apiFetch（cookie 鉴权、401 跳转、ApiError 归一），错误
 * scope 归入 "knowledge"；URL 保持 app 相对路径（/api/knowledge 薄代理），
 * 由 apiFetch 层统一处理子路径部署。上游 `{code, data, message}` 信封在
 * unwrapEnvelope 解包；上传用 FormData（apiFetch 不覆写 Content-Type，
 * fetch 自动带 boundary）。
 */

import { requestJson } from "@/shared/api/client";

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

class KnowledgeApiError extends Error {
  readonly code: number;
  constructor(code: number, message: string) {
    super(message);
    this.code = code;
  }
}

/** rag-app 信封：code=0 成功；否则按业务错误抛出（message 来自上游）。 */
function unwrapEnvelope<T>(payload: unknown): T {
  if (payload && typeof payload === "object" && "code" in (payload as Record<string, unknown>)) {
    const envelope = payload as { code?: unknown; data?: unknown; message?: unknown };
    const code = typeof envelope.code === "number" ? envelope.code : 0;
    if (code !== 0) {
      throw new KnowledgeApiError(
        code,
        typeof envelope.message === "string" && envelope.message
          ? envelope.message
          : `Upstream error (code ${code})`,
      );
    }
    return envelope.data as T;
  }
  return payload as T;
}

async function requestKnowledge<T>(
  path: string,
  init: Parameters<typeof requestJson>[1] = {},
): Promise<T> {
  const payload = await requestJson<unknown>(path, {
    ...init,
    scope: "knowledge",
  });
  return unwrapEnvelope<T>(payload);
}

// —— 状态 ——

export interface KnowledgeStatus {
  enabled: boolean;
  identity_ok: boolean | null;
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
  return parseKnowledgeDatasets({ data });
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
  return parseKnowledgeDatasets({ data: [inner] })[0] ?? null;
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
  return parseKnowledgeDatasets({ data: [inner] })[0] ?? null;
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
  return parseKnowledgeDocuments({ data });
}

export async function uploadDocuments(
  datasetId: string,
  files: File[],
): Promise<void> {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  form.append("type", "local");
  const payload = await requestJson<unknown>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents`,
    { method: "POST", body: form, scope: "knowledge" },
  );
  unwrapEnvelope(payload);
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
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: documentIds }) },
  );
}

export async function stopParsing(
  datasetId: string,
  documentIds: string[],
): Promise<void> {
  await requestKnowledge<void>(
    `/api/knowledge-center/datasets/${encodeURIComponent(datasetId)}/documents/stop`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: documentIds }) },
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

export { KnowledgeApiError };
