"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Pencil, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/Button";

import { deleteChunks, listChunks, updateChunk } from "../api";
import type { KnowledgeChunk, KnowledgeDocument } from "../model";

/**
 * 分块管理面板（P2-T9）：模态列表——内容预览/展开、available 启停、关键词徽标、
 * 行内编辑、两步删除。不做新建（方案 D4）。分页 20/页。
 */

const PAGE_SIZE = 20;

export default function ChunkListPanel({
  datasetId,
  doc,
  onClose,
}: {
  datasetId: string;
  doc: KnowledgeDocument | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [chunks, setChunks] = useState<KnowledgeChunk[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const load = useCallback(
    async (targetPage?: number) => {
      if (!doc) return;
      try {
        const result = await listChunks(datasetId, doc.id, {
          page: targetPage ?? page,
          signal: undefined,
        });
        setChunks(result.chunks);
        setTotal(result.total);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [datasetId, doc, page],
  );

  useEffect(() => {
    if (!doc) return;
    setChunks(null);
    setExpanded(new Set());
    setEditingId(null);
    setConfirmDeleteId(null);
    void load();
    // doc 切换或页码变化时重拉；load 的 page 依赖随调用链刷新
  }, [doc?.id, page]); // eslint-disable-line react-hooks/exhaustive-deps -- load 随 page 变化

  useEffect(() => {
    if (!doc) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [doc, onClose]);

  // 上游 chunk 变更为异步落库：紧跟的 GET 可能读到旧值（T9 live 实测）。
  // 因此变更一律先改本地状态，延迟重拉以服务端为准，期间不让回读覆盖乐观值。
  // （reloadSoon 必须在 early return 之前声明——Rules of Hooks）
  const reloadTimerRef = useRef<number | null>(null);
  const reloadSoon = useCallback(() => {
    if (reloadTimerRef.current != null) window.clearTimeout(reloadTimerRef.current);
    reloadTimerRef.current = window.setTimeout(() => void load(), 900);
  }, [load]);
  useEffect(
    () => () => {
      if (reloadTimerRef.current != null) window.clearTimeout(reloadTimerRef.current);
    },
    [],
  );

  if (!doc) return null;

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const toggleAvailable = async (chunk: KnowledgeChunk) => {
    const target = !chunk.available;
    setChunks(
      (prev) =>
        prev ? prev.map((item) => (item.id === chunk.id ? { ...item, available: target } : item)) : prev,
    );
    try {
      await updateChunk(datasetId, doc.id, chunk.id, { available: target });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      reloadSoon();
    }
  };

  const saveEdit = async (chunk: KnowledgeChunk) => {
    setChunks((prev) =>
      prev ? prev.map((item) => (item.id === chunk.id ? { ...item, content: editDraft } : item)) : prev,
    );
    setEditingId(null);
    try {
      await updateChunk(datasetId, doc.id, chunk.id, { content: editDraft });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      reloadSoon();
    }
  };

  const removeChunk = async (chunkId: string) => {
    setChunks((prev) => (prev ? prev.filter((item) => item.id !== chunkId) : prev));
    setTotal((prev) => Math.max(0, prev - 1));
    setConfirmDeleteId(null);
    try {
      await deleteChunks(datasetId, doc.id, [chunkId]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      reloadSoon();
    }
  };

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={doc.name}>
      <button
        type="button"
        aria-label={t("Close")}
        className="absolute inset-0 h-full w-full cursor-default bg-black/40"
        onClick={onClose}
      />
      <div className="absolute left-1/2 top-1/2 flex max-h-[86vh] w-[min(760px,94vw)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-xl">
        <div className="flex items-center gap-2 border-b border-[var(--border)]/60 px-4 py-3">
          <span className="rounded-md border border-[var(--border)] bg-[var(--muted)]/50 px-1.5 py-0.5 text-[11px] font-medium text-[var(--muted-foreground)]">
            {t("Chunks")}
          </span>
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-[var(--foreground)]" title={doc.name}>
            {doc.name}
          </span>
          <span className="shrink-0 text-[11.5px] text-[var(--muted-foreground)]">{total}</span>
          <Button variant="ghost" size="sm" icon={<X size={14} />} onClick={onClose} aria-label={t("Close")} />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {error ? <p className="mb-2 text-[12.5px] text-destructive">{error}</p> : null}
          {chunks == null ? (
            <p className="py-6 text-center text-[12.5px] text-[var(--muted-foreground)]">
              {t("Loading...")}
            </p>
          ) : chunks.length === 0 ? (
            <p className="py-6 text-center text-[12.5px] text-[var(--muted-foreground)]">
              {t("No chunks in this document yet. Parse the document first.")}
            </p>
          ) : (
            <ul className="space-y-2.5">
              {chunks.map((chunk) => {
                const open = expanded.has(chunk.id);
                const editing = editingId === chunk.id;
                return (
                  <li
                    key={chunk.id}
                    className={`rounded-lg border px-3.5 py-3 ${
                      chunk.available
                        ? "border-[var(--border)]/50 bg-[var(--background)]"
                        : "border-[var(--border)]/50 bg-[var(--muted)]/30 opacity-70"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <label className="flex items-center gap-1.5 text-[11.5px] text-[var(--muted-foreground)]">
                        <input
                          type="checkbox"
                          checked={chunk.available}
                          disabled={saving}
                          onChange={() => toggleAvailable(chunk)}
                        />
                        {chunk.available ? t("Enabled") : t("Disabled")}
                      </label>
                      <div className="flex items-center gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          icon={<Pencil size={12} />}
                          disabled={saving}
                          onClick={() => {
                            setEditingId(chunk.id);
                            setEditDraft(chunk.content);
                          }}
                        >
                          {t("Edit")}
                        </Button>
                        {confirmDeleteId === chunk.id ? (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={saving}
                              onClick={() => removeChunk(chunk.id)}
                            >
                              {t("Confirm delete?")}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setConfirmDeleteId(null)}
                            >
                              {t("Cancel")}
                            </Button>
                          </>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            icon={<Trash2 size={12} />}
                            disabled={saving}
                            onClick={() => setConfirmDeleteId(chunk.id)}
                          >
                            {t("Delete")}
                          </Button>
                        )}
                      </div>
                    </div>

                    {editing ? (
                      <div className="mt-2">
                        <textarea
                          value={editDraft}
                          rows={5}
                          onChange={(event) => setEditDraft(event.target.value)}
                          className="w-full resize-y rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 font-mono text-[12px] leading-relaxed text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60"
                        />
                        <div className="mt-1.5 flex justify-end gap-2">
                          <Button variant="ghost" size="sm" onClick={() => setEditingId(null)}>
                            {t("Cancel")}
                          </Button>
                          <Button
                            variant="primary"
                            size="sm"
                            loading={saving}
                            onClick={() => saveEdit(chunk)}
                          >
                            {t("Save")}
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <p
                        className={`mt-2 whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-[var(--foreground)] ${
                          open ? "" : "line-clamp-4"
                        }`}
                      >
                        {chunk.content || t("(empty chunk)")}
                      </p>
                    )}

                    {!editing ? (
                      <>
                        {chunk.importantKeywords.length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {chunk.importantKeywords.map((keyword) => (
                              <span
                                key={keyword}
                                className="rounded-md border border-[var(--border)] bg-[var(--muted)]/40 px-1.5 py-0.5 text-[10.5px] text-[var(--muted-foreground)]"
                              >
                                {keyword}
                              </span>
                            ))}
                          </div>
                        ) : null}
                        <button
                          type="button"
                          className="mt-1.5 text-[11px] text-[var(--muted-foreground)] underline-offset-2 hover:underline"
                          onClick={() =>
                            setExpanded((prev) => {
                              const next = new Set(prev);
                              if (next.has(chunk.id)) next.delete(chunk.id);
                              else next.add(chunk.id);
                              return next;
                            })
                          }
                        >
                          {open ? t("Collapse") : t("Expand")}
                        </button>
                      </>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {pages > 1 ? (
          <div className="flex items-center justify-end gap-3 border-t border-[var(--border)]/60 px-4 py-2.5 text-[12px] text-[var(--muted-foreground)]">
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
              onClick={() => setPage(page - 1)}
            >
              {t("Previous")}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={page >= pages}
              onClick={() => setPage(page + 1)}
            >
              {t("Next")}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
