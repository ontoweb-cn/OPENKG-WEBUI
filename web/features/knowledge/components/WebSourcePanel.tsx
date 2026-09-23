"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Globe, Loader2, RefreshCw, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import {
  deleteWebSource,
  fetchWebSource,
  saveWebSource,
  syncWebSource,
  type WebSource,
} from "../api";

/**
 * 外部源·Web 爬取（Phase 2 T5）：站点 URL + 页数/深度配置 + 手动同步。
 * 同站 BFS 抓取 → HTML 转 Markdown → 增量入库（服务端后台任务）。
 */

export default function WebSourcePanel({ datasetId }: { datasetId: string }) {
  const { t } = useTranslation();
  const [exists, setExists] = useState(false);
  const [state, setState] = useState<Record<string, unknown>>({});
  const [baseUrl, setBaseUrl] = useState("");
  const [maxPages, setMaxPages] = useState(20);
  const [maxDepth, setMaxDepth] = useState(2);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const source = await fetchWebSource(datasetId);
      if (source) {
        setExists(true);
        setBaseUrl(source.base_url);
        setMaxPages(source.max_pages || 20);
        setMaxDepth(source.max_depth || 2);
        setState(source.state || {});
      } else {
        setExists(false);
        setState({});
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [datasetId]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    if (!baseUrl.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await saveWebSource(datasetId, {
        base_url: baseUrl.trim(),
        max_pages: maxPages,
        max_depth: maxDepth,
      });
      setExists(true);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const sync = async () => {
    setSaving(true);
    setError(null);
    try {
      await syncWebSource(datasetId);
      setState((prev) => ({ ...prev, sync_status: "running" }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setSaving(true);
    try {
      await deleteWebSource(datasetId);
      setExists(false);
      setBaseUrl("");
      setState({});
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const syncStatus = typeof state.sync_status === "string" ? state.sync_status : "";

  return (
    <section className="mt-8">
      <h2 className="mb-3 flex items-center gap-2 text-[14px] font-semibold text-[var(--foreground)]">
        <Globe size={15} strokeWidth={1.8} />
        {t("Web source")}
      </h2>
      <div className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4">
        <label className="block text-[12.5px] font-medium text-[var(--foreground)]">
          {t("Site URL")}
          <input
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://docs.example.com/guide"
            className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] outline-none focus:border-[var(--primary)]/60"
          />
        </label>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <label className="block text-[12.5px] font-medium text-[var(--foreground)]">
            {t("Max pages")}
            <input
              type="number"
              min={1}
              max={100}
              value={maxPages}
              onChange={(event) => setMaxPages(Number(event.target.value) || 20)}
              className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] outline-none focus:border-[var(--primary)]/60"
            />
          </label>
          <label className="block text-[12.5px] font-medium text-[var(--foreground)]">
            {t("Max depth")}
            <input
              type="number"
              min={1}
              max={5}
              value={maxDepth}
              onChange={(event) => setMaxDepth(Number(event.target.value) || 2)}
              className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] outline-none focus:border-[var(--primary)]/60"
            />
          </label>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button variant="primary" loading={saving} onClick={save}>
            {t("Save source")}
          </Button>
          {exists ? (
            <Button variant="secondary" icon={<RefreshCw size={13} />} loading={saving} onClick={sync}>
              {t("Sync now")}
            </Button>
          ) : null}
          {exists ? (
            <Button variant="ghost" icon={<Trash2 size={13} />} onClick={remove}>
              {t("Remove source")}
            </Button>
          ) : null}
          {syncStatus ? (
            <span className="text-[12px] text-[var(--muted-foreground)]">
              {t("Sync status:")} {syncStatus}
              {typeof state.last_result === "string" && state.last_result
                ? ` — ${state.last_result}`
                : ""}
            </span>
          ) : null}
          {error ? <span className="text-[12px] text-destructive">{error}</span> : null}
        </div>
        {saving ? (
          <p className="mt-2 flex items-center gap-1.5 text-[12px] text-[var(--muted-foreground)]">
            <Loader2 size={12} className="animate-spin" />
            {t("Working...")}
          </p>
        ) : null}
      </div>
    </section>
  );
}
