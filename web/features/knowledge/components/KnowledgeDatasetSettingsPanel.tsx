"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/Button";

import { fetchKnowledgePreferences, putKnowledgePreferences } from "../api";
import {
  formatBytes,
  type KnowledgeDataset,
  type KnowledgeVisibility,
} from "../model";
/**
 * 知识库 Settings tab（P0-T4）：元数据只读区 + per-user 默认知识库开关。
 * 偏好走既有代理端点 GET/PUT /preferences（knowledge.py:217/238），
 * 载荷 `{"default_dataset_id": string|null}`（空串=清除）。
 */

export default function KnowledgeDatasetSettingsPanel({
  dataset,
}: {
  dataset: KnowledgeDataset | null;
}) {
  const { t } = useTranslation();
  const [isDefault, setIsDefault] = useState(false);
  const [prefsLoading, setPrefsLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPrefsLoading(true);
    fetchKnowledgePreferences()
      .then((prefs) => {
        if (cancelled) return;
        setIsDefault(prefs.defaultDatasetId != null && prefs.defaultDatasetId === dataset?.id);
      })
      .catch(() => {
        if (!cancelled) setIsDefault(false);
      })
      .finally(() => {
        if (!cancelled) setPrefsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [dataset?.id]);

  const onToggleDefault = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const next = isDefault ? null : dataset?.id ?? null;
      await putKnowledgePreferences(next);
      setIsDefault(!isDefault);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [dataset?.id, isDefault]);

  if (!dataset) {
    return (
      <div className="flex h-28 items-center justify-center text-sm text-[var(--muted-foreground)]">
        {t("Loading...")}
      </div>
    );
  }

  const visibilityLabel: Record<KnowledgeVisibility, string> = {
    private: t("Private"),
    tenant: t("Tenant visible"),
    team: t("Team visible"),
    project: t("Project only"),
  };

  return (
    <div className="max-w-3xl space-y-4">
      <section className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4">
        <h3 className="mb-3 text-[13px] font-semibold text-[var(--foreground)]">
          {t("Dataset info")}
        </h3>
        <dl className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-2 text-[12.5px]">
          <dt className="text-[var(--muted-foreground)]">{t("Visibility")}</dt>
          <dd className="text-[var(--foreground)]">{visibilityLabel[dataset.visibility]}</dd>
          <dt className="text-[var(--muted-foreground)]">{t("Documents")}</dt>
          <dd className="text-[var(--foreground)]">{dataset.documentCount}</dd>
          <dt className="text-[var(--muted-foreground)]">{t("Chunks")}</dt>
          <dd className="text-[var(--foreground)]">{dataset.chunkCount}</dd>
          <dt className="text-[var(--muted-foreground)]">{t("Tokens")}</dt>
          <dd className="text-[var(--foreground)]">
            {dataset.tokenCount > 0 ? formatBytes(dataset.tokenCount) : "—"}
          </dd>
          <dt className="text-[var(--muted-foreground)]">{t("Created")}</dt>
          <dd className="text-[var(--foreground)]">{dataset.createdAt || "—"}</dd>
        </dl>
      </section>

      <section className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4">
        <h3 className="mb-1 text-[13px] font-semibold text-[var(--foreground)]">
          {t("Default knowledge base")}
        </h3>
        <p className="mb-3 text-[12px] text-[var(--muted-foreground)]">
          {t("The default knowledge base is preselected when attaching knowledge in chat.")}
        </p>
        <label className="flex items-center gap-2 text-[12.5px] text-[var(--foreground)]">
          <input
            type="checkbox"
            checked={isDefault}
            disabled={prefsLoading || saving}
            onChange={onToggleDefault}
          />
          {t("Set this knowledge base as my default")}
        </label>
        {saving ? (
          <Button variant="ghost" size="sm" loading className="mt-2 pointer-events-none">
            {t("Saving...")}
          </Button>
        ) : null}
        {error ? <p className="mt-2 text-[12.5px] text-destructive">{error}</p> : null}
      </section>
    </div>
  );
}
