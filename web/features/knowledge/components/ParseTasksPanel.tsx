"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, RotateCw, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/Button";

import { fetchIngestionLogs } from "../api";
import type { KnowledgeDocument, KnowledgeIngestionLog } from "../model";

/**
 * 解析任务面板（P1-T6/R4）：数据 = 当前页文档的 RUNNING 进度 + 既有
 * ingestions 端点的历史（log_type=file）。不做上传队列（上传无任务化端点）。
 */

function RunBadge({ run }: { run: KnowledgeIngestionLog["status"] }) {
  const { t } = useTranslation();
  const styles: Record<string, string> = {
    DONE: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
    RUNNING: "border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-400",
    FAIL: "border-destructive/30 bg-destructive/10 text-destructive",
    CANCEL: "border-[var(--border)] bg-[var(--muted)]/50 text-[var(--muted-foreground)]",
    UNSTART: "border-[var(--border)] bg-[var(--muted)]/50 text-[var(--muted-foreground)]",
  };
  const labels: Record<string, string> = {
    DONE: t("Done"),
    RUNNING: t("Parsing"),
    FAIL: t("Failed"),
    CANCEL: t("Cancelled"),
    UNSTART: t("Pending"),
  };
  return (
    <span
      className={`inline-block shrink-0 rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${styles[run] ?? styles.UNSTART}`}
    >
      {labels[run] ?? run}
    </span>
  );
}

export default function ParseTasksPanel({
  datasetId,
  documents,
  busy,
  onRetryFailed,
}: {
  datasetId: string;
  documents: KnowledgeDocument[] | null;
  busy: boolean;
  onRetryFailed: () => void;
}) {
  const { t } = useTranslation();
  const [logs, setLogs] = useState<KnowledgeIngestionLog[] | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showHistory, setShowHistory] = useState(false);

  // 历史日志：dataset 变化拉取，Refresh 按钮手动重拉（logTick 触发）
  const [logTick, setLogTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    fetchIngestionLogs(datasetId)
      .then((rows) => {
        if (!cancelled) setLogs(rows);
      })
      .catch(() => {
        if (!cancelled) setLogs(null);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId, logTick]);

  const runningDocs = (documents ?? []).filter((doc) => doc.run === "RUNNING");
  const failedCount = (documents ?? []).filter((doc) => doc.run === "FAIL").length;

  if (!documents || (runningDocs.length === 0 && (logs == null || logs.length === 0))) {
    return null;
  }

  const toggleRow = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <section className="mb-4 rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[13px] font-semibold text-[var(--foreground)]">
          {t("Parse tasks")}
        </h3>
        <div className="flex items-center gap-2">
          {failedCount > 0 ? (
            <Button
              variant="secondary"
              size="sm"
              icon={<RotateCw size={13} />}
              disabled={busy}
              onClick={onRetryFailed}
            >
              {t("Retry {{count}} failed", { count: failedCount })}
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            icon={<RefreshCw size={13} />}
            onClick={() => setLogTick((value) => value + 1)}
          >
            {t("Refresh")}
          </Button>
        </div>
      </div>

      {runningDocs.map((doc) => (
        <div key={doc.id} className="mt-3">
          <div className="flex items-center justify-between gap-2 text-[12px] text-[var(--foreground)]">
            <span className="min-w-0 truncate" title={doc.name}>
              {doc.name}
            </span>
            <span className="shrink-0 text-[var(--muted-foreground)]">
              {Math.floor(doc.progress)}%
            </span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--muted)]">
            <div
              className="h-full rounded-full bg-[var(--primary)] transition-all"
              style={{ width: `${Math.max(3, Math.min(100, doc.progress))}%` }}
            />
          </div>
        </div>
      ))}

      {logs != null && logs.length > 0 ? (
        <div className="mt-3 border-t border-[var(--border)]/50 pt-2.5">
          <button
            type="button"
            className="text-[12px] text-[var(--muted-foreground)] underline-offset-2 hover:underline"
            onClick={() => setShowHistory((value) => !value)}
          >
            {showHistory ? t("Hide parse history") : t("Show parse history ({{count}})", { count: logs.length })}
          </button>
          {showHistory ? (
            <ul className="mt-2 space-y-1">
              {logs.map((log) => {
                const open = expanded.has(log.id);
                return (
                  <li key={log.id} className="text-[12px]">
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-[var(--muted)]/40"
                      onClick={() => toggleRow(log.id)}
                    >
                      {open ? (
                        <ChevronDown size={12} className="shrink-0 text-[var(--muted-foreground)]" />
                      ) : (
                        <ChevronRight size={12} className="shrink-0 text-[var(--muted-foreground)]" />
                      )}
                      <RunBadge run={log.status} />
                      <span className="min-w-0 flex-1 truncate text-[var(--foreground)]" title={log.documentName}>
                        {log.documentName}
                      </span>
                      <span className="shrink-0 text-[var(--muted-foreground)]">
                        {Math.floor(log.progress)}%
                      </span>
                    </button>
                    {open && log.message ? (
                      <pre className="ml-6 max-h-32 overflow-y-auto whitespace-pre-wrap break-words rounded bg-[var(--muted)]/30 p-2 font-mono text-[11px] leading-relaxed text-[var(--muted-foreground)]">
                        {log.message}
                      </pre>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
