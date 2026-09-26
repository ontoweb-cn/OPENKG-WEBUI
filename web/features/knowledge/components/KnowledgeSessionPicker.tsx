"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { BookOpen, Check, ChevronDown, Loader2, X } from "lucide-react";

import { fetchDatasets } from "../api";
import type { KnowledgeDataset } from "../model";
import {
  getSessionKnowledgeSelection,
  loadSessionKnowledgeSelection,
  readDraftKnowledgeSelection,
  saveSessionKnowledgeSelection,
  writeDraftKnowledgeSelection,
} from "../session-selection";

/**
 * 会话级知识库勾选（Phase 1.5）：composer 上方的紧凑选择条。勾选集持久化
 * 到会话偏好，turn 发送时经 `knowledge_bases` 字段透传为网关
 * `rag.knowledge_base_ids`（精确召回）。
 */

export default function KnowledgeSessionPicker({
  sessionId,
  onChanged,
  variant = "bar",
}: {
  /** 会话 id；null = 新会话草稿模式（选择暂存内存，首轮 turn 携带后转正） */
  sessionId: string | null;
  onChanged?: (kbIds: string[]) => void;
  /** bar = composer 上方独立条（默认）；toolbar = composer 工具行内（对齐模型选择器） */
  variant?: "bar" | "toolbar";
}) {
  const { t } = useTranslation();
  const [datasets, setDatasets] = useState<KnowledgeDataset[] | null>(null);
  // 草稿（sessionId=null）在惰性初始化时读内存草稿键——避免 effect 内同步
  // setState（lint react-hooks）；真实会话由下方 effect 异步回填
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(sessionId ? [] : readDraftKnowledgeSelection()),
  );
  const [open, setOpen] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    // 真实会话：回填已保存的勾选（跨刷新保持）。
    // null → 真实 id 的转正瞬间跳过一次回填：适配器正在异步持久化草稿选择，
    // 立即回读可能读到旧偏好而清掉用户刚勾的内容（阶段 4 live 修正）。
    if (sessionId) {
      loadSessionKnowledgeSelection(sessionId).then((ids) => {
        if (cancelled) return;
        setSelected(new Set(ids));
      });
    }
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
        const ids = [...next];
        if (sessionId) {
          void saveSessionKnowledgeSelection(sessionId, ids).catch(() => undefined);
        } else {
          // 草稿：写内存草稿键，BIND_SERVER_SESSION 时由适配器转正
          writeDraftKnowledgeSelection(ids);
        }
        onChanged?.(ids);
        return next;
      });
    },
    [sessionId, onChanged],
  );

  if (unavailable || (datasets != null && datasets.length === 0)) return null;

  const selectedCount = selected.size;

  const toolbar = variant === "toolbar";
  return (
    <div ref={rootRef} className={toolbar ? "relative" : "relative mb-1.5"}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={
          toolbar
            ? `inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2 text-[12.5px] transition-[background-color,color,transform] duration-150 active:scale-[0.97] ${
                open
                  ? "bg-[var(--muted)] text-[var(--foreground)]"
                  : selectedCount > 0
                    ? "bg-[var(--accent)]/60 text-[var(--foreground)]"
                    : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
              }`
            : `inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[12px] transition-colors ${
                selectedCount > 0
                  ? "border-[var(--primary)]/40 bg-[var(--accent)]/60 text-[var(--foreground)]"
                  : "border-[var(--border)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              }`
        }
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
        <div
          className={`absolute bottom-full z-40 mb-1.5 w-72 rounded-xl border border-[var(--border)] bg-[var(--popover)] p-1.5 shadow-lg ${
            toolbar ? "right-0" : "left-0"
          }`}
        >
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
