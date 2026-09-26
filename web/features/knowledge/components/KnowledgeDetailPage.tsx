"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTranslation } from "react-i18next";
import {
  Database,
  FileUp,
  FolderUp,
  RotateCw,
  Search,
  Square,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import FilePreviewDrawer from "@/components/chat/preview/FilePreviewDrawer";
import type { FilePreviewSource } from "@/components/chat/preview/previewerFor";
import {
  deleteDocuments,
  fetchDataset,
  fetchDocuments,
  fetchIngestionLogs,
  logsStreamUrl,
  parseDocuments,
  previewUrl,
  searchDataset,
  stopParsing,
  uploadDocuments,
  uploadStructured,
} from "../api";
import GithubSourcePanel from "./GithubSourcePanel";
import ParseTasksPanel from "./ParseTasksPanel";
import WebSourcePanel from "./WebSourcePanel";
import KnowledgeDatasetSettingsPanel from "./KnowledgeDatasetSettingsPanel";
import {
  formatBytes,
  anyDocumentRunning,
  type KnowledgeDataset,
  type KnowledgeDocument,
  type KnowledgeIngestionLog,
  type KnowledgeSearchChunk,
} from "../model";
import { precheckUploadFiles, type PrecheckItem } from "../upload-precheck";
import {
  KnowledgeBackLink,
  KnowledgePageBody,
  KnowledgePageHeader,
} from "./KnowledgePageFrame";

/**
 * 知识库详情页（Phase 1a T7 → P0-T1 标签页化）：
 * documents（列表/拖放上传/删除/重解析/停止 + SSE 日志）/ sources / retrieval /
 * settings 四 tab，`?section=` 深链。进度轮询（D5：活跃期 4s）与 SSE 日志订阅
 * 挂在页面层，切 tab 不重建。文档 run 状态机语义 Derived from DeepMentor
 * （Apache-2.0），modified。
 */

const POLL_INTERVAL_MS = 4000;

const SECTIONS = ["documents", "sources", "retrieval", "settings"] as const;
type DetailSection = (typeof SECTIONS)[number];

function normalizeSection(value: string | null): DetailSection {
  return (SECTIONS as readonly string[]).includes(value ?? "") ? (value as DetailSection) : "documents";
}

export default function KnowledgeDetailPage({ datasetId }: { datasetId: string }) {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const section = normalizeSection(searchParams.get("section"));

  const [dataset, setDataset] = useState<KnowledgeDataset | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [deleteIds, setDeleteIds] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [liveLogs, setLiveLogs] = useState<string[]>([]);
  // P0-T2：预览源（文件名驱动渲染器分流，url 指向代理 preview 字节流）
  const [previewSource, setPreviewSource] = useState<FilePreviewSource | null>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const running = documents ? anyDocumentRunning(documents) : false;

  const switchSection = useCallback(
    (next: DetailSection) => {
      router.replace(`?section=${next}`, { scroll: false });
    },
    [router],
  );

  // T3：解析进行中时订阅代理 SSE 日志流；非 JSON/错误帧静默忽略，
  // 断开自动回退到文档列表轮询。
  useEffect(() => {
    if (!running) return;
    const es = new EventSource(logsStreamUrl(datasetId));
    es.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as {
          logs?: Array<Record<string, unknown>>;
        };
        const lines = (payload.logs ?? [])
          .map((log) => {
            // T3（D6）：SSE 帧为域形状——message 即上游 progress_msg 的归一
            const docName = typeof log.document_name === "string" ? log.document_name : "";
            const progress = typeof log.progress === "number" ? Math.floor(log.progress) : 0;
            const message = typeof log.message === "string" ? log.message : "";
            const tail = message.split("\n").pop() ?? "";
            return `[${progress}%] ${docName}: ${tail}`;
          })
          .filter((line) => line.trim().length > 0)
          .reverse();
        setLiveLogs(lines);
      } catch {
        /* 非 JSON 帧（keepalive 等）忽略 */
      }
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [datasetId, running]);

  useEffect(() => {
    fetchDataset(datasetId)
      .then((value) => {
        if (value) setDataset(value);
      })
      .catch(() => {
        // 元数据解析失败不阻塞详情页——Settings 面板回落 loading 态
      });
  }, [datasetId]);

  // P1-T5：服务端分页（page_size=50）+ 页内筛选（R2：先翻页再过滤）
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "DONE" | "RUNNING" | "FAIL">("all");
  const PAGE_SIZE = 50;

  const load = useCallback(
    async (signal?: AbortSignal, targetPage?: number) => {
      try {
        const result = await fetchDocuments(datasetId, {
          page: targetPage ?? page,
          signal,
        });
        setDocuments(result.documents);
        setTotal(result.total);
        setError(null);
      } catch (err) {
        if (signal?.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [datasetId, page],
  );

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    if (!running) return () => controller.abort();
    // D5：活跃期轮询，终态停表（DeepMentor useKnowledgeBases 的 4s 模式）
    const timer = setInterval(() => load(controller.signal), POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [load, running]);

  const gotoPage = useCallback(
    (next: number) => {
      setPage(next);
      // load 依赖 page state，直接以目标页发起，避免等待状态合流
      void load(undefined, next);
    },
    [load],
  );

  const onUpload = async (fileList: FileList | File[] | null) => {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      // zip 走结构化端点（代理解包，D1）；其余走多文件直传
      const files = Array.from(fileList);
      const zips = files.filter((f) => f.name.toLowerCase().endsWith(".zip"));
      const plain = files.filter((f) => !f.name.toLowerCase().endsWith(".zip"));
      if (plain.length) await uploadDocuments(datasetId, plain);
      for (const zf of zips) {
        await uploadStructured(datasetId, [{ file: zf }]);
      }
      if (plain.length || zips.length) await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  const onUploadFolder = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      const entries = Array.from(fileList).map((f) => ({
        file: f,
        relPath:
          (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name,
      }));
      await uploadStructured(datasetId, entries);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      if (folderInputRef.current) folderInputRef.current.value = "";
    }
  };

  const onDelete = async () => {
    if (!deleteIds) return;
    setBusy(true);
    try {
      await deleteDocuments(datasetId, deleteIds);
      setDeleteIds(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setDeleteIds(null);
    } finally {
      setBusy(false);
    }
  };

  const onReparse = async (doc: KnowledgeDocument) => {
    setBusy(true);
    setError(null);
    try {
      await parseDocuments(datasetId, [doc.id]);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onStop = async () => {
    const runningIds = (documents ?? [])
      .filter((doc) => doc.run === "RUNNING")
      .map((doc) => doc.id);
    if (runningIds.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await stopParsing(datasetId, runningIds);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // P1-T6：一键重试当前页全部失败文档
  const onRetryFailed = async () => {
    const failedIds = (documents ?? []).filter((doc) => doc.run === "FAIL").map((doc) => doc.id);
    if (failedIds.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await parseDocuments(datasetId, failedIds);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const tabLabel: Record<DetailSection, string> = {
    documents: t("Documents"),
    sources: t("Sources"),
    retrieval: t("Retrieval"),
    settings: t("Settings"),
  };

  return (
    <KnowledgePageBody>
      <KnowledgeBackLink href="/knowledge-center" label={t("Back to knowledge bases")} />
      <KnowledgePageHeader
        icon={Database}
        title={dataset?.name || datasetId}
        description={t("Documents are parsed and indexed by Intellect RAG after upload.")}
        action={
          <div className="flex items-center gap-2">
            {running ? (
              <Button variant="secondary" icon={<Square size={13} />} loading={busy} onClick={onStop}>
                {t("Stop parsing")}
              </Button>
            ) : null}
            <Button variant="secondary" icon={<FolderUp size={14} />} onClick={() => folderInputRef.current?.click()}>
              {t("Upload folder")}
            </Button>
            <input
              ref={folderInputRef}
              type="file"
              multiple
              // @ts-expect-error 非标准属性：目录上传
              webkitdirectory=""
              className="hidden"
              onChange={(event) => onUploadFolder(event.target.files)}
            />
          </div>
        }
      />

      {error ? (
        <p className="mb-4 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-2.5 text-[12.5px] text-[var(--foreground)]">
          {error}
        </p>
      ) : null}

      {/* P0-T1：下划线式 tab（对齐 DeepMentor KnowledgeBaseDetail）；面板常驻
          挂载以 hidden 切换，保住轮询/SSE/表单状态（P0 评审 R5） */}
      <div className="mb-4 flex gap-5 border-b border-[var(--border)]/60" role="tablist">
        {SECTIONS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={section === key}
            onClick={() => switchSection(key)}
            className={`-mb-px border-b-2 px-0.5 pb-2 text-[13px] transition-colors ${
              section === key
                ? "border-[var(--primary)] font-medium text-[var(--foreground)]"
                : "border-transparent text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            }`}
          >
            {tabLabel[key]}
          </button>
        ))}
      </div>

      <div className={section === "documents" ? "" : "hidden"}>
        <UploadDropzone uploading={uploading} onUpload={onUpload} />
        <ParseTasksPanel
          datasetId={datasetId}
          documents={documents}
          busy={busy}
          onRetryFailed={onRetryFailed}
        />
        <DocumentFilterBar
          search={search}
          statusFilter={statusFilter}
          onSearch={setSearch}
          onStatusFilter={setStatusFilter}
        />
        <DocumentTable
          documents={documents}
          search={search}
          statusFilter={statusFilter}
          busy={busy}
          onRequestDelete={(ids) => setDeleteIds(ids)}
          onReparse={onReparse}
          onOpenPreview={(doc) =>
            setPreviewSource({
              filename: doc.name,
              url: previewUrl(doc.id),
              size: doc.size,
              id: doc.id,
            })
          }
        />
        <PagePager
          page={page}
          total={total}
          pageSize={PAGE_SIZE}
          onPage={gotoPage}
        />
        {running && liveLogs.length > 0 ? (
          <div className="mt-4 rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-3">
            <p className="mb-1.5 text-[11.5px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
              {t("Live parse logs")}
            </p>
            <ul className="max-h-40 space-y-0.5 overflow-y-auto font-mono text-[11px] text-[var(--muted-foreground)]">
              {liveLogs.map((line, index) => (
                <li key={`${index}-${line.slice(0, 12)}`} className="truncate" title={line}>
                  {line}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      <div className={section === "sources" ? "max-w-3xl" : "hidden"}>
        <GithubSourcePanel datasetId={datasetId} />
        <WebSourcePanel datasetId={datasetId} />
      </div>

      <div className={section === "retrieval" ? "" : "hidden"}>
        <RetrievalPlayground datasetId={datasetId} />
      </div>

      <div className={section === "settings" ? "" : "hidden"}>
        <KnowledgeDatasetSettingsPanel dataset={dataset} onUpdated={setDataset} />
      </div>

      <ConfirmDialog
        open={deleteIds != null}
        title={t("Delete documents")}
        tone="danger"
        busy={busy}
        confirmLabel={t("Delete")}
        onCancel={() => setDeleteIds(null)}
        onConfirm={onDelete}
      >
        {t("\"{{name}}\" will be deleted.", {
          name: t("{{count}} selected documents", { count: deleteIds?.length ?? 0 }),
        })}
      </ConfirmDialog>

      {/* P0-T2：复用聊天附件预览抽屉（media preview 的 raw fetch 白名单成员） */}
      <FilePreviewDrawer
        open={previewSource !== null}
        source={previewSource}
        onClose={() => setPreviewSource(null)}
      />
    </KnowledgePageBody>
  );
}

/**
 * 拖放上传区（P0-T3）：拖放/点击选择 → 预校验摘要（去重、扩展名提示、
 * 单文件移除）→ 确认上传。文件夹上传仍走页头按钮（webkitdirectory 直传）。
 */
function UploadDropzone({
  uploading,
  onUpload,
}: {
  uploading: boolean;
  onUpload: (files: File[] | null) => void;
}) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const [dragActive, setDragActive] = useState(false);
  const [staged, setStaged] = useState<PrecheckItem[]>([]);

  const stageFiles = (files: FileList | File[] | null) => {
    if (!files || files.length === 0) return;
    // 与既有 staged 合并后再统一去重（precheckUploadFiles 按 name+size 剔重）
    const merged = [...staged.map((item) => item.file), ...Array.from(files)];
    setStaged(precheckUploadFiles(merged));
  };

  const submit = () => {
    if (staged.length === 0) return;
    onUpload(staged.map((item) => item.file));
    setStaged([]);
  };

  return (
    <div className="mb-4">
      <div
        role="button"
        tabIndex={0}
        aria-label={t("Upload files")}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
        }}
        onDragEnter={(event) => {
          event.preventDefault();
          dragDepth.current += 1;
          setDragActive(true);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={() => {
          // 深度计数防抖：子元素进出也会触发 dragleave（DeepMentor FileDropZone 同款）
          dragDepth.current -= 1;
          if (dragDepth.current <= 0) {
            dragDepth.current = 0;
            setDragActive(false);
          }
        }}
        onDrop={(event) => {
          event.preventDefault();
          dragDepth.current = 0;
          setDragActive(false);
          stageFiles(event.dataTransfer.files);
        }}
        className={`flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed px-6 py-7 text-center transition-colors ${
          dragActive
            ? "border-[var(--primary)]/70 bg-[var(--primary)]/5"
            : "border-[var(--border)]/70 bg-[var(--card)]/50 hover:border-[var(--border)]"
        }`}
      >
        <UploadCloud size={18} className="text-[var(--muted-foreground)]" />
        <p className="text-[13px] text-[var(--foreground)]">
          {t("Drag and drop files here, or click to select files.")}
        </p>
        <p className="text-[11.5px] text-[var(--muted-foreground)]">
          {t("Zip archives are unpacked server-side; folder uploads keep the directory layout.")}
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => {
            stageFiles(event.target.files);
            event.target.value = "";
          }}
        />
      </div>

      {staged.length > 0 ? (
        <div className="mt-2 rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[12.5px] text-[var(--foreground)]">
              {t("{{count}} files selected", { count: staged.length })}
            </p>
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="sm" onClick={() => setStaged([])}>
                {t("Clear")}
              </Button>
              <Button
                variant="primary"
                size="sm"
                icon={<FileUp size={13} />}
                loading={uploading}
                onClick={submit}
              >
                {t("Upload {{count}} files", { count: staged.length })}
              </Button>
            </div>
          </div>
          <ul className="mt-2 max-h-44 space-y-1 overflow-y-auto">
            {staged.map((item) => (
              <li
                key={`${item.file.name}-${item.file.size}`}
                className="flex items-center gap-2 text-[12px]"
              >
                <span className="min-w-0 flex-1 truncate text-[var(--foreground)]">
                  {item.file.name}
                </span>
                <span className="shrink-0 text-[var(--muted-foreground)]">
                  {formatBytes(item.file.size)}
                </span>
                <span
                  className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium ${
                    item.status === "supported"
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                      : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400"
                  }`}
                >
                  {item.status === "supported" ? t("Supported") : t("Unrecognized type")}
                </span>
                <button
                  type="button"
                  aria-label={t("Remove")}
                  className="shrink-0 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                  onClick={() =>
                    setStaged((prev) =>
                      prev.filter((candidate) => candidate.file !== item.file),
                    )
                  }
                >
                  <X size={13} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/** P1-T5：location 父目录分组键（根级文档归 "root" 组）。 */
function dirOf(location: string): string {
  const idx = location.lastIndexOf("/");
  return idx > 0 ? location.slice(0, idx) : "/";
}

function DocumentFilterBar({
  search,
  statusFilter,
  onSearch,
  onStatusFilter,
}: {
  search: string;
  statusFilter: "all" | "DONE" | "RUNNING" | "FAIL";
  onSearch: (value: string) => void;
  onStatusFilter: (value: "all" | "DONE" | "RUNNING" | "FAIL") => void;
}) {
  const { t } = useTranslation();
  const statuses = [
    { key: "all", label: t("All") },
    { key: "DONE", label: t("Done") },
    { key: "RUNNING", label: t("Parsing") },
    { key: "FAIL", label: t("Failed") },
  ] as const;
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <input
        value={search}
        onChange={(event) => onSearch(event.target.value)}
        placeholder={t("Filter by name (current page)")}
        className="w-56 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[12.5px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60"
      />
      <div className="flex items-center gap-1" role="group" aria-label={t("Status")}>
        {statuses.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => onStatusFilter(item.key)}
            className={`rounded-md border px-2 py-1 text-[11.5px] transition-colors ${
              statusFilter === item.key
                ? "border-[var(--primary)]/50 bg-[var(--primary)]/10 text-[var(--foreground)]"
                : "border-[var(--border)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function PagePager({
  page,
  total,
  pageSize,
  onPage,
}: {
  page: number;
  total: number;
  pageSize: number;
  onPage: (next: number) => void;
}) {
  const { t } = useTranslation();
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <div className="mt-3 flex items-center justify-end gap-3 text-[12px] text-[var(--muted-foreground)]">
      <span>
        {t("Page {{page}} of {{pages}} · {{count}} documents", {
          page,
          pages,
          count: total,
        })}
      </span>
      <Button
        variant="secondary"
        size="sm"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        {t("Previous")}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
      >
        {t("Next")}
      </Button>
    </div>
  );
}

function DocumentTable({
  documents,
  search,
  statusFilter,
  busy,
  onRequestDelete,
  onReparse,
  onOpenPreview,
}: {
  documents: KnowledgeDocument[] | null;
  search: string;
  statusFilter: "all" | "DONE" | "RUNNING" | "FAIL";
  busy: boolean;
  onRequestDelete: (ids: string[]) => void;
  onReparse: (doc: KnowledgeDocument) => void;
  onOpenPreview: (doc: KnowledgeDocument) => void;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  if (documents == null) {
    return (
      <div className="flex h-28 items-center justify-center text-sm text-[var(--muted-foreground)]">
        {t("Loading...")}
      </div>
    );
  }
  if (documents.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--border)]/70 bg-[var(--card)]/50 px-6 py-10 text-center text-[13px] text-[var(--muted-foreground)]">
        {t("No documents yet. Upload files to start indexing.")}
      </div>
    );
  }

  // R2：先服务端翻页、再页内过滤（搜索 + 状态）
  const keyword = search.trim().toLowerCase();
  const filtered = documents.filter(
    (doc) =>
      (keyword === "" || doc.name.toLowerCase().includes(keyword)) &&
      (statusFilter === "all" || doc.run === statusFilter),
  );

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (filtered.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--border)]/70 bg-[var(--card)]/50 px-6 py-8 text-center text-[13px] text-[var(--muted-foreground)]">
        {t("No matches on this page.")}
      </div>
    );
  }

  // 目录分组：保持首次出现顺序（T5）
  const groups: Array<{ dir: string; rows: KnowledgeDocument[] }> = [];
  for (const doc of filtered) {
    const dir = dirOf(doc.location);
    const bucket = groups.find((group) => group.dir === dir);
    if (bucket) bucket.rows.push(doc);
    else groups.push({ dir, rows: [doc] });
  }

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--border)]/60 bg-[var(--card)]">
      {selected.size > 0 ? (
        <div className="flex items-center gap-3 border-b border-[var(--border)]/60 px-4 py-2 text-[12.5px] text-[var(--muted-foreground)]">
          <span>{t("{{count}} selected documents", { count: selected.size })}</span>
          <Button
            variant="ghost"
            size="sm"
            icon={<Trash2 size={13} />}
            onClick={() => onRequestDelete([...selected])}
          >
            {t("Delete")}
          </Button>
        </div>
      ) : null}
      <table className="w-full text-left text-[13px]">
        <thead>
          <tr className="border-b border-[var(--border)]/60 text-[11.5px] uppercase tracking-wide text-[var(--muted-foreground)]">
            <th className="w-9 px-4 py-2.5">
              <input
                type="checkbox"
                aria-label={t("Select all")}
                checked={selected.size === filtered.length && filtered.length > 0}
                onChange={(event) =>
                  setSelected(
                    event.target.checked ? new Set(filtered.map((d) => d.id)) : new Set(),
                  )
                }
              />
            </th>
            <th className="px-2 py-2.5 font-medium">{t("Name")}</th>
            <th className="px-2 py-2.5 font-medium">{t("Status")}</th>
            <th className="px-2 py-2.5 font-medium">{t("Chunks")}</th>
            <th className="px-2 py-2.5 font-medium">{t("Size")}</th>
            <th className="px-4 py-2.5" />
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <GroupRows
              key={group.dir}
              dir={group.dir}
              rows={group.rows}
              selected={selected}
              busy={busy}
              onToggle={toggle}
              onRequestDelete={onRequestDelete}
              onReparse={onReparse}
              onOpenPreview={onOpenPreview}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GroupRows({
  dir,
  rows,
  selected,
  busy,
  onToggle,
  onRequestDelete,
  onReparse,
  onOpenPreview,
}: {
  dir: string;
  rows: KnowledgeDocument[];
  selected: Set<string>;
  busy: boolean;
  onToggle: (id: string) => void;
  onRequestDelete: (ids: string[]) => void;
  onReparse: (doc: KnowledgeDocument) => void;
  onOpenPreview: (doc: KnowledgeDocument) => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      {dir !== "/" ? (
        <tr className="bg-[var(--muted)]/30">
          <td colSpan={6} className="px-4 py-1.5 text-[11.5px] font-medium text-[var(--muted-foreground)]">
            {dir}/
          </td>
        </tr>
      ) : null}
      {rows.map((doc) => (
        <tr key={doc.id} className="border-b border-[var(--border)]/40 last:border-0">
          <td className="px-4 py-2.5">
            <input
              type="checkbox"
              aria-label={doc.name}
              checked={selected.has(doc.id)}
              onChange={() => onToggle(doc.id)}
            />
          </td>
          <td className="max-w-72 truncate px-2 py-2.5" title={doc.location}>
            <button
              type="button"
              className="truncate text-left text-[var(--foreground)] underline-offset-2 hover:underline"
              onClick={() => onOpenPreview(doc)}
            >
              {doc.name}
            </button>
          </td>
          <td className="px-2 py-2.5">
            <RunBadge run={doc.run} progress={doc.progress} />
          </td>
          <td className="px-2 py-2.5 text-[var(--muted-foreground)]">{doc.chunkCount}</td>
          <td className="px-2 py-2.5 text-[var(--muted-foreground)]">{formatBytes(doc.size)}</td>
          <td className="px-4 py-2.5">
            <div className="flex items-center justify-end gap-1.5">
              {doc.run === "DONE" || doc.run === "FAIL" ? (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  icon={<RotateCw size={13} />}
                  onClick={() => onReparse(doc)}
                >
                  {t("Reparse")}
                </Button>
              ) : null}
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                icon={<Trash2 size={13} />}
                onClick={() => onRequestDelete([doc.id])}
              >
                {t("Delete")}
              </Button>
            </div>
          </td>
        </tr>
      ))}
    </>
  );
}

function RunBadge({ run, progress }: { run: KnowledgeDocument["run"]; progress: number }) {
  const { t } = useTranslation();
  const styles: Record<KnowledgeDocument["run"], string> = {
    DONE: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
    RUNNING: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
    FAIL: "border-destructive/30 bg-destructive/10 text-destructive",
    CANCEL: "border-[var(--border)] bg-[var(--muted)]/50 text-[var(--muted-foreground)]",
    UNSTART: "border-[var(--border)] bg-[var(--muted)]/50 text-[var(--muted-foreground)]",
  };
  const labels: Record<KnowledgeDocument["run"], string> = {
    DONE: t("Done"),
    RUNNING: t("Running {{percent}}%", { percent: Math.floor(progress) }),
    FAIL: t("Failed"),
    CANCEL: t("Cancelled"),
    UNSTART: t("Pending"),
  };
  return (
    <span
      className={`inline-block rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${styles[run]}`}
    >
      {labels[run]}
    </span>
  );
}

function RetrievalPlayground({ datasetId }: { datasetId: string }) {
  const { t } = useTranslation();
  const [question, setQuestion] = useState("");
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chunks, setChunks] = useState<KnowledgeSearchChunk[] | null>(null);
  const [logs, setLogs] = useState<KnowledgeIngestionLog[] | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // P1-T7：参数开放（缺省=引擎缺省口径，R5 只开放三项）
  const [threshold, setThreshold] = useState(0.2);
  const [topK, setTopK] = useState(1024);
  const [vectorWeight, setVectorWeight] = useState(0.3);
  const [showParams, setShowParams] = useState(false);

  const onSearch = async () => {
    if (!question.trim()) return;
    setSearching(true);
    setError(null);
    try {
      setChunks(
        await searchDataset(datasetId, question.trim(), {
          similarityThreshold: threshold,
          topK,
          vectorSimilarityWeight: vectorWeight,
        }),
      );
      fetchIngestionLogs(datasetId)
        .then(setLogs)
        .catch(() => setLogs(null));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSearching(false);
    }
  };

  const toggleChunk = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <section>
      <h2 className="mb-3 text-[14px] font-semibold text-[var(--foreground)]">
        {t("Retrieval playground")}
      </h2>
      <div className="max-w-3xl rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4">
        <div className="flex gap-2">
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") onSearch();
            }}
            placeholder={t("Ask a question to test retrieval in this knowledge base.")}
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60"
          />
          <Button
            variant="primary"
            icon={<Search size={14} />}
            loading={searching}
            disabled={!question.trim()}
            onClick={onSearch}
          >
            {t("Search")}
          </Button>
        </div>

        <div className="mt-2">
          <button
            type="button"
            className="text-[11.5px] text-[var(--muted-foreground)] underline-offset-2 hover:underline"
            onClick={() => setShowParams((value) => !value)}
          >
            {showParams ? t("Hide parameters") : t("Retrieval parameters")}
          </button>
          {showParams ? (
            <div className="mt-2 grid grid-cols-3 gap-3">
              <label className="text-[11.5px] text-[var(--muted-foreground)]">
                {t("Similarity threshold")}
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={threshold}
                  onChange={(event) => setThreshold(Number(event.target.value))}
                  className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-[12.5px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60"
                />
              </label>
              <label className="text-[11.5px] text-[var(--muted-foreground)]">
                {t("Top K")}
                <input
                  type="number"
                  min={1}
                  max={1024}
                  step={1}
                  value={topK}
                  onChange={(event) => setTopK(Number(event.target.value))}
                  className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-[12.5px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60"
                />
              </label>
              <label className="text-[11.5px] text-[var(--muted-foreground)]">
                {t("Vector weight")}
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={vectorWeight}
                  onChange={(event) => setVectorWeight(Number(event.target.value))}
                  className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-[12.5px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60"
                />
              </label>
            </div>
          ) : null}
        </div>

        {error ? <p className="mt-3 text-[12.5px] text-destructive">{error}</p> : null}

        {chunks != null && chunks.length === 0 ? (
          <p className="mt-3 text-[12.5px] text-[var(--muted-foreground)]">
            {t("No matching chunks. Try a different question or lower the threshold.")}
          </p>
        ) : null}

        {chunks != null && chunks.length > 0 ? (
          <ul className="mt-3 space-y-2.5">
            {chunks.map((chunk) => {
              const open = expanded.has(chunk.id);
              return (
                <li
                  key={chunk.id}
                  className="rounded-lg border border-[var(--border)]/50 bg-[var(--background)] px-3.5 py-3"
                >
                  <div className="flex items-center justify-between gap-2 text-[11.5px] text-[var(--muted-foreground)]">
                    <span className="truncate">{chunk.documentName}</span>
                    <span className="shrink-0">{(chunk.similarity * 100).toFixed(1)}%</span>
                  </div>
                  <p
                    className={`mt-1.5 whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--foreground)] ${
                      open ? "" : "line-clamp-4"
                    }`}
                  >
                    {stripHighlightTags(chunk.content)}
                  </p>
                  <button
                    type="button"
                    className="mt-1 text-[11px] text-[var(--muted-foreground)] underline-offset-2 hover:underline"
                    onClick={() => toggleChunk(chunk.id)}
                  >
                    {open ? t("Collapse") : t("Expand")}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}

        {logs != null && logs.length > 0 ? (
          <div className="mt-4 border-t border-[var(--border)]/50 pt-3">
            <button
              type="button"
              className="text-[12px] text-[var(--muted-foreground)] underline-offset-2 hover:underline"
              onClick={() => setShowLogs((value) => !value)}
            >
              {showLogs ? t("Hide recent parse logs") : t("Show recent parse logs")}
            </button>
            {showLogs ? (
              <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto font-mono text-[11px] leading-relaxed text-[var(--muted-foreground)]">
                {logs.map((log) => (
                  <li key={log.id} className="truncate" title={log.message}>
                    [{log.status} {Math.floor(log.progress)}%] {log.documentName}: {log.message}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
      <p className="mt-2 max-w-3xl text-[11.5px] text-[var(--muted-foreground)]">
        {t("Tip: attach this knowledge base in chat to let agents retrieve from it.")}{" "}
        <Link href="/chat" className="underline underline-offset-2">
          {t("Open Chat")}
        </Link>
      </p>
    </section>
  );
}

/** 检索高亮返回带 <b>/<em> 标签的片段——纯文本展示前剥除标签（非富文本场景）。 */
function stripHighlightTags(content: string): string {
  return content.replace(/<\/?(b|em|strong|mark)>/g, "");
}
