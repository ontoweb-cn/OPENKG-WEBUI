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
  /** legacy 创建期望值（"me" | "team"）；**不参与访问控制**，勿作徽标数据源 */
  permission: string;
  /** 实际可见范围（访问控制依据） */
  visibility: KnowledgeVisibility;
  documentCount: number;
  chunkCount: number;
  tokenCount: number;
  /** 上游 embedding 模型名（只读展示） */
  embeddingModel: string;
  /** 上游 create_time（epoch 毫秒串） */
  createdAt: string;
}

/** 上游 `visibility` 列：private | tenant | team | project */
export type KnowledgeVisibility = "private" | "tenant" | "team" | "project";

function normalizeVisibility(value: unknown): KnowledgeVisibility {
  const v = text(value).toLowerCase();
  if (v === "tenant" || v === "team" || v === "project") return v;
  // 未知/缺失按 private 展示（保守；不暗示比实际更宽的范围）
  return "private";
}

export function parseKnowledgeDataset(raw: unknown): KnowledgeDataset {
  const row = record(raw);
  return {
    id: text(row.id),
    name: text(row.name),
    description: text(row.description),
    permission: text(row.permission) || "me",
    visibility: normalizeVisibility(row.visibility),
    documentCount: num(row.document_count),
    chunkCount: num(row.chunk_count),
    tokenCount: num(row.token_count),
    // 后端域字段是 created_at（T3 收口）；create_time 是上游列名——
    // 此前读错侧导致创建时间恒为空（收尾批次 R2）
    createdAt: text(row.created_at) || text(row.create_time),
    embeddingModel: text(row.embedding_model),
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

// —— per-user 偏好（默认知识库，Phase 1.5 T4 代理端点的域形状）——

export interface KnowledgePreferences {
  /** 默认知识库 id；null = 未设置。 */
  defaultDatasetId: string | null;
}

export function parseKnowledgePreferences(raw: unknown): KnowledgePreferences {
  const row = record(raw);
  const id = text(row.default_dataset_id).trim();
  return { defaultDatasetId: id.length > 0 ? id : null };
}

// —— chunk 管理（P2-T9；上游 available_int 为权威，available 键可 null）——

export interface KnowledgeChunk {
  id: string;
  content: string;
  available: boolean;
  importantKeywords: string[];
}

export function parseKnowledgeChunk(raw: unknown): KnowledgeChunk {
  const row = record(raw);
  const available = row.available ?? row.available_int;
  const keywords = Array.isArray(row.important_keywords)
    ? row.important_keywords.map((k) => text(k)).filter((k) => k.length > 0)
    : [];
  return {
    id: text(row.id),
    content: text(row.content),
    available: available == null ? true : Boolean(available),
    importantKeywords: keywords,
  };
}

export function parseKnowledgeChunks(raw: unknown): {
  chunks: KnowledgeChunk[];
  total: number;
} {
  const row = record(raw);
  const rows = Array.isArray(row.chunks) ? row.chunks : [];
  let total = 0;
  const n = Number(row.total);
  total = Number.isFinite(n) ? n : rows.length;
  return { chunks: rows.map(parseKnowledgeChunk), total };
}

// —— 知识图谱（P2-T10；未构建时 nodes/edges 为空）——

export interface GraphNode {
  id: string;
  label: string;
  /** 原始载荷其余键（entity_type 等），画布着色可用 */
  raw: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  label: string;
  weight: number;
}

export interface KnowledgeGraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export function parseKnowledgeGraph(raw: unknown): KnowledgeGraphData {
  const row = record(raw);
  const graph = record(row.graph);
  const nodeRows = Array.isArray(graph.nodes) ? graph.nodes : [];
  const edgeRows = Array.isArray(graph.edges) ? graph.edges : [];
  const nodes: GraphNode[] = nodeRows.map((n) => {
    const r = record(n);
    const id = text(r.id);
    const name = text(r.entity_name) || text(r.name) || text(r.label) || id;
    return { id, label: name, raw: r };
  });
  const edges: GraphEdge[] = edgeRows.map((e) => {
    const r = record(e);
    const w = Number(r.weight);
    return {
      source: text(r.source),
      target: text(r.target),
      label: text(r.description) || text(r.relationship) || "",
      weight: Number.isFinite(w) ? w : 1,
    };
  });
  return { nodes, edges };
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

/** 上游 create_time（epoch 毫秒串）→ 本地 "YYYY-MM-DD HH:mm"；无效输入原样返回。 */
export function formatEpochMillis(raw: string): string {
  if (!raw) return "";
  const millis = Number(raw);
  if (!Number.isFinite(millis) || millis <= 0) return raw;
  const date = new Date(millis);
  if (Number.isNaN(date.getTime())) return raw;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 文档类型图标（扩展名 → 语义色徽标文本）；对齐 DeepMentor docIconFor 的概念。 */export function docIconFor(name: string): { label: string; className: string } {
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 && dot < name.length - 1 ? name.slice(dot + 1).toLowerCase() : "";
  const table: Record<string, { label: string; className: string }> = {
    pdf: { label: "PDF", className: "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/30" },
    doc: { label: "DOC", className: "bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30" },
    docx: { label: "DOCX", className: "bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30" },
    ppt: { label: "PPT", className: "bg-orange-500/10 text-orange-700 dark:text-orange-400 border-orange-500/30" },
    pptx: { label: "PPTX", className: "bg-orange-500/10 text-orange-700 dark:text-orange-400 border-orange-500/30" },
    xls: { label: "XLS", className: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30" },
    xlsx: { label: "XLSX", className: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30" },
    csv: { label: "CSV", className: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30" },
    md: { label: "MD", className: "bg-purple-500/10 text-purple-700 dark:text-purple-400 border-purple-500/30" },
    png: { label: "IMG", className: "bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-400 border-fuchsia-500/30" },
    jpg: { label: "IMG", className: "bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-400 border-fuchsia-500/30" },
    jpeg: { label: "IMG", className: "bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-400 border-fuchsia-500/30" },
    svg: { label: "SVG", className: "bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-400 border-fuchsia-500/30" },
    zip: { label: "ZIP", className: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30" },
    txt: { label: "TXT", className: "bg-[var(--muted)] text-[var(--muted-foreground)] border-[var(--border)]" },
  };
  return (
    table[ext] ?? {
      label: ext.slice(0, 4).toUpperCase() || "FILE",
      className: "bg-[var(--muted)] text-[var(--muted-foreground)] border-[var(--border)]",
    }
  );
}

// —— 进度文案最小映射（P3 T14'；D3：上游 progress_msg 为自由文本，无 message_key）——

/** i18n 翻译函数形状（避免模型层依赖 react-i18next）。 */
export type TranslateFn = (key: string, options?: Record<string, unknown>) => string;

/**
 * 从上游 progress_msg 片段识别已知的失败/完成原因，给出本地化提示；
 * 无命中的片段原样展示（调用方兜底）——映射表是解释性补充，不是整行替换。
 */
const PROGRESS_HINT_PATTERNS: Array<{ pattern: RegExp; key: string }> = [
  // 实测出现过的两类真实失败（P2 T10 / 收尾批次）
  { pattern: /Provider\s+not found for model/i, key: "The RAG server has no chat model configured for this operation." },
  { pattern: /dimension \(\d+\) of given embedding model is different/i, key: "Embedding dimension mismatch — the vector index must be rebuilt." },
  { pattern: /\[ERROR\]|Exception/i, key: "An error occurred during parsing." },
  { pattern: /task finished|finished successfully|completed/i, key: "Parsing completed." },
];

export function progressHint(raw: string, t: TranslateFn): string | null {
  if (!raw) return null;
  for (const entry of PROGRESS_HINT_PATTERNS) {
    if (entry.pattern.test(raw)) return t(entry.key);
  }
  return null;
}
