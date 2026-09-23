"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Github, Loader2, RefreshCw, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import {
  deleteGithubSource,
  fetchGithubSource,
  saveGithubSource,
  syncGithubSource,
} from "../api";

/**
 * 外部源·GitHub（Phase 2 T4）：repo/branch/glob 配置 + 手动同步 + 状态。
 * 同步状态由服务端后台任务维护（store），本面板轮询展示。
 */

export default function GithubSourcePanel({ datasetId }: { datasetId: string }) {
  const { t } = useTranslation();
  const [exists, setExists] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("main");
  const [prefix, setPrefix] = useState("");
  const [glob, setGlob] = useState("*.md");
  const [token, setToken] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const source = await fetchGithubSource(datasetId);
      if (source) {
        setExists(true);
        setRepo(source.repo);
        setBranch(source.branch || "main");
        setPrefix(source.path_prefix || "");
        setGlob(source.glob || "*.md");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [datasetId]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    if (!repo.trim() || !repo.includes("/")) {
      setError(t("Repo must look like owner/name"));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const body: Record<string, string> = {
        repo: repo.trim(),
        branch: branch.trim() || "main",
        glob: glob.trim() || "*.md",
      };
      if (prefix.trim()) body.path_prefix = prefix.trim();
      if (token.trim()) body.token = token.trim();
      const source = await saveGithubSource(datasetId, body);
      setExists(true);
      setToken("");
      setError(null);
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
      await syncGithubSource(datasetId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setSaving(true);
    try {
      await deleteGithubSource(datasetId);
      setExists(false);
      setRepo("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const busy = saving || loading;

  return (
    <section className="mt-8">
      <h2 className="mb-3 flex items-center gap-2 text-[14px] font-semibold text-[var(--foreground)]">
        <Github size={15} strokeWidth={1.8} />
        {t("GitHub source")}
      </h2>
      <div className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4">
        <label className="block text-[12.5px] font-medium text-[var(--foreground)]">
          {t("Repo (owner/name)")}
          <input
            value={repo}
            onChange={(event) => setRepo(event.target.value)}
            placeholder="octocat/Hello-World"
            className={`mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60`}
          />
        </label>
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
          <label className="block text-[12.5px] font-medium text-[var(--foreground)]">
            {t("Branch")}
            <input
              value={branch}
              onChange={(event) => setBranch(event.target.value)}
              className={`mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] outline-none focus:border-[var(--primary)]/60`}
            />
          </label>
          <label className="block text-[12.5px] font-medium text-[var(--foreground)]">
            {t("Path prefix")}
            <input
              value={prefix}
              onChange={(event) => setPrefix(event.target.value)}
              placeholder="docs/"
              className={`mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] outline-none focus:border-[var(--primary)]/60`}
            />
          </label>
          <label className="block text-[12.5px] font-medium text-[var(--foreground)]">
            {t("Glob")}
            <input
              value={glob}
              onChange={(event) => setGlob(event.target.value)}
              className={`mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] outline-none focus:border-[var(--primary)]/60`}
            />
          </label>
        </div>
        <label className="mt-3 block text-[12.5px] font-medium text-[var(--foreground)]">
          {t("Token (optional)")}
          <input
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder={t("Leave blank to keep")}
            className={`mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] outline-none focus:border-[var(--primary)]/60`}
          />
        </label>
        <div className="mt-4 flex items-center gap-2">
          <Button variant="primary" loading={saving} onClick={save}>
            {t("Save source")}
          </Button>
          {exists ? (
            <Button
              variant="secondary"
              icon={<RefreshCw size={13} />}
              loading={saving}
              onClick={sync}
            >
              {t("Sync now")}
            </Button>
          ) : null}
          {exists ? (
            <Button variant="ghost" icon={<Trash2 size={13} />} onClick={remove}>
              {t("Remove source")}
            </Button>
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
