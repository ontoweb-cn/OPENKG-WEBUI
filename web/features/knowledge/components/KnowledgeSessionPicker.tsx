"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BookOpen, Check, ChevronDown, Loader2, X } from "lucide-react";

import { fetchDatasets } from "../api";
import type { KnowledgeDataset } from "../model";
import {
  getSessionKnowledgeSelection,
  loadSessionKnowledgeSelection,
  saveSessionKnowledgeSelection,
} from "../session-selection";

/**
 * 会话级知识库勾选（Phase 1.5）：composer 上方的紧凑选择条。勾选集持久化
 * 到会话偏好，turn 发送时经 `knowledge_bases` 字段透传为网关
 * `rag.knowledge_base_ids`（精确召回）。
 */

export default function KnowledgeSessionPicker({
  sessionId,
  onChanged,
}: {
  sessionId: string;
  onChanged?: (kbIds: string[]) => void;
}) {
  const { t } = useTranslation();
  const [datasets, setDatasets] = useState<KnowledgeDataset[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    // 回填本会话已保存的勾选（跨刷新保持）
    loadSessionKnowledgeSelection(sessionId).then((ids) => {
      if (cancelled) return;
      setSelected(new Set(ids));
    });
    fetchDatasets()
      .then((list) => {
        if (cancelled) return;
        setDatasets(list);
      })
      .catch(() => {
        if (!cancelled) setUnavailable(true);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const toggle = useCallback(
    (id: string) => {
      setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        void saveSessionKnowledgeSelection(sessionId, [...next]).catch(() => undefined);
        onChanged?.([...next]);
        return next;
      });
    },
    [sessionId, onChanged],
  );

  if (unavailable || (datasets != null && datasets.length === 0)) return null;

  const selectedCount = selected.size;

  return (
    <div ref={rootRef} className="relative mb-1.5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[12px] transition-colors ${
          selectedCount > 0
            ? "border-[var(--primary)]/40 bg-[var(--accent)]/60 text-[var(--foreground)]"
            : "border-[var(--border)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
        }`}
      >
        <BookOpen size={13} strokeWidth={1.8} />
        {selectedCount > 0
          ? t("{{count}} knowledge bases", { count: selectedCount })
          : t("Attach knowledge bases")}
        <ChevronDown
          size={12}
          strokeWidth={2}
          className={`transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open ? (
        <div className="absolute bottom-full z-40 mb-1.5 w-72 rounded-xl border border-[var(--border)] bg-[var(--popover)] p-1.5 shadow-lg">
          {datasets == null ? (
            <div className="flex items-center gap-2 px-3 py-2 text-[12.5px] text-[var(--muted-foreground)]">
              <Loader2 size={13} className="animate-spin" />
              {t("Loading...")}
            </div>
          ) : datasets.length === 0 ? (
            <p className="px-3 py-2 text-[12.5px] text-[var(--muted-foreground)]">
              {t("No knowledge bases yet")}
            </p>
          ) : (
            <ul role="listbox" aria-label={t("Knowledge bases")} className="max-h-64 overflow-y-auto">
              {datasets.map((dataset) => {
                const checked = selected.has(dataset.id);
                return (
                  <li key={dataset.id}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={checked}
                      onClick={() => toggle(dataset.id)}
                      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors ${
                        checked
                          ? "bg-[var(--accent)] text-[var(--foreground)]"
                          : "text-[var(--foreground)]/85 hover:bg-[var(--muted)]/55"
                      }`}
                    >
                      {checked ? (
                        <Check size={14} strokeWidth={2.2} />
                      ) : (
                        <span className="inline-block w-[14px]" />
                      )}
                      <span className="min-w-0 flex-1 truncate">{dataset.name}</span>
                      <span className="shrink-0 text-[11px] text-[var(--muted-foreground)]">
                        {dataset.documentCount}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}

export function KnowledgeSelectionChips({
  ids,
  onRemove,
}: {
  ids: string[];
  onRemove?: (id: string) => void;
}) {
  const { t } = useTranslation();
  if (ids.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {ids.map((id) => (
        <span
          key={id}
          className="inline-flex items-center gap-1 rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-1.5 py-0.5 text-[11px] text-[var(--muted-foreground)]"
        >
          <BookOpen size={11} />
          {id.slice(0, 8)}
          {onRemove ? (
            <button type="button" aria-label={t("Delete")} onClick={() => onRemove(id)}>
              <X size={11} />
            </button>
          ) : null}
        </span>
      ))}
    </div>
  );
}
