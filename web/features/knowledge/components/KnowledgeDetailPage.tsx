"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import {
  Database,
  FileUp,
  FolderUp,
  RotateCw,
  Search,
  Square,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  deleteDocuments,
  fetchDataset,
  fetchDocuments,
  fetchIngestionLogs,
  logsStreamUrl,
  parseDocuments,
  searchDataset,
  stopParsing,
  uploadDocuments,
  uploadStructured,
} from "../api";
import GithubSourcePanel from "./GithubSourcePanel";
import {
  formatBytes,
  anyDocumentRunning,
  type KnowledgeDocument,
  type KnowledgeIngestionLog,
  type KnowledgeSearchChunk,
} from "../model";
import {
  KnowledgeBackLink,
  KnowledgePageBody,
  KnowledgePageHeader,
} from "./KnowledgePageFrame";

/**
 * 知识库详情页（Phase 1a T7）：文档 tab（列表/多文件上传/删除/重解析/停止）
 * + 进度轮询（D5：活跃期 4s 轮询文档列表）+ 检索试玩面板（D3：新增面）。
 * 文档 run 状态机语义 Derived from DeepMentor（Apache-2.0），modified。
 */

const POLL_INTERVAL_MS = 4000;

export default function KnowledgeDetailPage({ datasetId }: { datasetId: string }) {
  const { t } = useTranslation();

  const [name, setName] = useState(datasetId);
  const [documents, setDocuments] = useState<KnowledgeDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [deleteIds, setDeleteIds] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchDataset(datasetId)
      .then((dataset) => {
        if (dataset?.name) setName(dataset.name);
      })
      .catch(() => {
        // 名称解析失败不阻塞详情页——回落显示 id
      });
  }, [datasetId]);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const result = await fetchDocuments(datasetId, signal);
        setDocuments(result.documents);
        setError(null);
      } catch (err) {
        if (signal?.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [datasetId],
  );

  const running = documents ? anyDocumentRunning(documents) : false;

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

  const onUpload = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      // zip 走结构化端点（代理解包，D1）；其余走多文件直传
      const zips = Array.from(fileList).filter((f) => f.name.toLowerCase().endsWith(".zip"));
      const plain = Array.from(fileList).filter((f) => !f.name.toLowerCase().endsWith(".zip"));
      if (plain.length) await uploadDocuments(datasetId, plain);
      for (const zf of zips) {
        await uploadStructured(datasetId, [{ file: zf }]);
      }
      if (plain.length || zips.length) await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
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

  return (
    <KnowledgePageBody>
      <KnowledgeBackLink href="/knowledge-center" label={t("Back to knowledge bases")} />
      <KnowledgePageHeader
        icon={Database}
        title={name}
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
            <Button
              variant="primary"
              icon={<FileUp size={15} />}
              loading={uploading}
              onClick={() => fileInputRef.current?.click()}
            >
              {t("Upload files")}
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(event) => onUpload(event.target.files)}
            />
          </div>
        }
      />

      {error ? (
        <p className="mb-4 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-2.5 text-[12.5px] text-[var(--foreground)]">
          {error}
        </p>
      ) : null}

      <DocumentTable
        documents={documents}
        busy={busy}
        onRequestDelete={(ids) => setDeleteIds(ids)}
        onReparse={onReparse}
      />

      <RetrievalPlayground datasetId={datasetId} />
      <GithubSourcePanel datasetId={datasetId} />

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
    </KnowledgePageBody>
  );
}

function DocumentTable({
  documents,
  busy,
  onRequestDelete,
  onReparse,
}: {
  documents: KnowledgeDocument[] | null;
  busy: boolean;
  onRequestDelete: (ids: string[]) => void;
  onReparse: (doc: KnowledgeDocument) => void;
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

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

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
                checked={selected.size === documents.length}
                onChange={(event) =>
                  setSelected(
                    event.target.checked ? new Set(documents.map((d) => d.id)) : new Set(),
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
          {documents.map((doc) => (
            <tr key={doc.id} className="border-b border-[var(--border)]/40 last:border-0">
              <td className="px-4 py-2.5">
                <input
                  type="checkbox"
                  aria-label={doc.name}
                  checked={selected.has(doc.id)}
                  onChange={() => toggle(doc.id)}
                />
              </td>
              <td className="max-w-72 truncate px-2 py-2.5 text-[var(--foreground)]" title={doc.location}>
                {doc.name}
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
        </tbody>
      </table>
    </div>
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

  const onSearch = async () => {
    if (!question.trim()) return;
    setSearching(true);
    setError(null);
    try {
      setChunks(await searchDataset(datasetId, question.trim()));
      fetchIngestionLogs(datasetId)
        .then(setLogs)
        .catch(() => setLogs(null));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSearching(false);
    }
  };

  return (
    <section className="mt-8">
      <h2 className="mb-3 text-[14px] font-semibold text-[var(--foreground)]">
        {t("Retrieval playground")}
      </h2>
      <div className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4">
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

        {error ? <p className="mt-3 text-[12.5px] text-destructive">{error}</p> : null}

        {chunks != null && chunks.length === 0 ? (
          <p className="mt-3 text-[12.5px] text-[var(--muted-foreground)]">
            {t("No matching chunks. Try a different question or lower the threshold.")}
          </p>
        ) : null}

        {chunks != null && chunks.length > 0 ? (
          <ul className="mt-3 space-y-2.5">
            {chunks.map((chunk) => (
              <li
                key={chunk.id}
                className="rounded-lg border border-[var(--border)]/50 bg-[var(--background)] px-3.5 py-3"
              >
                <div className="flex items-center justify-between gap-2 text-[11.5px] text-[var(--muted-foreground)]">
                  <span className="truncate">{chunk.documentName}</span>
                  <span className="shrink-0">{(chunk.similarity * 100).toFixed(1)}%</span>
                </div>
                <p className="mt-1.5 line-clamp-4 whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--foreground)]">
                  {stripHighlightTags(chunk.content)}
                </p>
              </li>
            ))}
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
      <p className="mt-2 text-[11.5px] text-[var(--muted-foreground)]">
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
