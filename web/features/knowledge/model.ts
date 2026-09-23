/**
 * 知识中心纯模型层（docs/knowledge-center-port-design.md §三；Phase 1a T5）。
 *
 * 只做契约归一与校验，不渲染、不依赖 app/components/context——
 * `feature-domain-does-not-render` 分层规则由 depcruise 守护。
 * 上游是 rag-app 的 `{code, data, message}` 信封 + snake_case 字段；
 * 这里归一为 camelCase 视图模型，字段缺失/类型漂移时降级为安全缺省值。
 *
 * 部分进度/状态语义 Derived from DeepMentor
 * (web/lib/knowledge-helpers.ts, Apache-2.0, (c) 2025 Data Intelligence Lab,
 * The University of Hong Kong), modified.
 */

// —— 宽松取值助手（沿 features/kag/model.ts 形状）——

export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function text(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function num(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}

// —— 知识库（rag-app dataset 行）——

export interface KnowledgeDataset {
  id: string;
  name: string;
  description: string;
  /** "me" | "team"（上游 ownership 注入；缺省按私有展示） */
  permission: string;
  documentCount: number;
  chunkCount: number;
  tokenCount: number;
  createdAt: string;
}

export function parseKnowledgeDataset(raw: unknown): KnowledgeDataset {
  const row = record(raw);
  return {
    id: text(row.id),
    name: text(row.name),
    description: text(row.description),
    permission: text(row.permission) || "me",
    documentCount: num(row.document_count),
    chunkCount: num(row.chunk_count),
    tokenCount: num(row.token_count),
    createdAt: text(row.create_time),
  };
}

export function parseKnowledgeDatasets(payload: unknown): KnowledgeDataset[] {
  // T3：后端出站为域形状 `{datasets, total}`（信封已在 provider 内解包）；
  // 保留裸数组兜底作为廉价保险。
  const inner = record(payload);
  const list = Array.isArray(inner.datasets)
    ? inner.datasets
    : Array.isArray(payload)
      ? payload
      : [];
  return list
    .map(parseKnowledgeDataset)
    .filter((item) => item.id && item.name);
}

// —— 文档（rag-app document 行；run 是上游解析状态机）——

/** UNSTART/RUNNING/DONE/FAIL/CANCEL（RagFlow 派生口径） */
export type DocumentRun = "UNSTART" | "RUNNING" | "DONE" | "FAIL" | "CANCEL";

export interface KnowledgeDocument {
  id: string;
  name: string;
  run: DocumentRun;
  /** 0-100（上游浮点） */
  progress: number;
  chunkCount: number;
  tokenCount: number;
  size: number;
  /** 上传时的相对路径（parent/文件名），用于目录感展示 */
  location: string;
}

function normalizeRun(value: unknown): DocumentRun {
  const run = text(value).toUpperCase();
  if (run === "RUNNING" || run === "DONE" || run === "FAIL" || run === "CANCEL")
    return run;
  return "UNSTART";
}

export function parseKnowledgeDocument(raw: unknown): KnowledgeDocument {
  const row = record(raw);
  return {
    id: text(row.id),
    name: text(row.name),
    run: normalizeRun(row.run),
    progress: Math.max(0, Math.min(100, num(row.progress))),
    chunkCount: num(row.chunk_count),
    tokenCount: num(row.token_count),
    size: num(row.size),
    location: text(row.location) || text(row.name),
  };
}

export function parseKnowledgeDocuments(
  payload: unknown,
): { documents: KnowledgeDocument[]; total: number } {
  // T3：域形状 `{documents, total}`（原 `data.docs`）
  const inner = record(payload);
  const docs = Array.isArray(inner.documents)
    ? inner.documents
    : Array.isArray(record(payload).docs)
      ? (record(payload).docs as unknown[])
      : [];
  return {
    documents: docs.map(parseKnowledgeDocument).filter((item) => item.id),
    total: num(inner.total),
  };
}

export function anyDocumentRunning(documents: KnowledgeDocument[]): boolean {
  return documents.some((doc) => doc.run === "RUNNING");
}

// —— 摄取记录（进度日志轮询；D5） ——

export interface KnowledgeIngestionLog {
  id: string;
  progress: number;
  message: string;
  status: string;
  documentName: string;
}

export function parseIngestionLogs(payload: unknown): KnowledgeIngestionLog[] {
  // T3：域形状为裸数组 `[IngestionLog]`；兼容携 logs 键的包装（SSE 流帧）
  const direct = Array.isArray(payload) ? payload : null;
  const wrapped = record(payload);
  const logs = direct ?? (Array.isArray(wrapped.logs) ? wrapped.logs : []);
  return logs.map((raw) => {
    const row = record(raw);
    return {
      id: text(row.id),
      progress: num(row.progress),
      // T3：域模型字段为 message/status（provider 内由 progress_msg/
      // operation_status 归一）；保留上游名兜底作为廉价保险。
      message: text(row.message ?? row.progress_msg),
      status: text(row.status ?? row.operation_status),
      documentName: text(row.document_name),
    };
  });
}

// —— 检索试玩（单库 /search） ——

export interface KnowledgeSearchChunk {
  id: string;
  content: string;
  similarity: number;
  documentName: string;
}

export function parseSearchChunks(payload: unknown): KnowledgeSearchChunk[] {
  // T3：域形状 `{chunks, total, denied_dataset_ids}`（provider 内完成字段映射）
  const inner = record(payload);
  const chunks = Array.isArray(inner.chunks) ? inner.chunks : [];
  return chunks.map((raw) => {
    const row = record(raw);
    return {
      // 运行时验收（R-6）确认的实际字段名：chunk_id / content_with_weight /
      // docnm_kwd（RagFlow 派生口径）；保留通用回退。
      id: text(row.chunk_id ?? row.id),
      content: text(row.content_with_weight ?? row.content),
      similarity: num(row.similarity),
      documentName: text(row.docnm_kwd ?? row.document_name),
    };
  });
}

// —— 工具 ——

export function formatBytes(size: number): string {
  if (size <= 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}
