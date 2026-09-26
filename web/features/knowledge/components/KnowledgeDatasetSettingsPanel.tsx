"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/Button";

import { fetchKnowledgePreferences, putKnowledgePreferences, updateDataset } from "../api";
import {
  formatBytes,
  formatEpochMillis,
  type KnowledgeDataset,
  type KnowledgeVisibility,
} from "../model";
/**
 * 知识库 Settings tab（P0-T4 元数据/默认库 + P1-T8 改名/描述）。
 * 偏好走既有代理端点 GET/PUT /preferences（knowledge.py:217/238），
 * 载荷 `{"default_dataset_id": string|null}`（空串=清除）。
 * 改名走 PUT /datasets/{id}，响应为回读后的服务端状态（R3）。
 */

export default function KnowledgeDatasetSettingsPanel({
  dataset,
  onUpdated,
}: {
  dataset: KnowledgeDataset | null;
  onUpdated?: (dataset: KnowledgeDataset) => void;
}) {
  const { t } = useTranslation();
  const [isDefault, setIsDefault] = useState(false);
  const [prefsLoading, setPrefsLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // P1-T8：编辑表单草稿——按 dataset id 键控派生（dataset 切换即回落到
  // 服务端值），避免 effect 内同步 setState 触发级联渲染（lint react-hooks）。
  const datasetKey = dataset?.id ?? "";
  const [draftState, setDraftState] = useState<{
    key: string;
    name: string;
    description: string;
    saved: boolean;
  }>({ key: "", name: "", description: "", saved: false });
  const drafts = useMemo(
    () =>
      draftState.key === datasetKey
        ? draftState
        : {
            key: datasetKey,
            name: dataset?.name ?? "",
            description: dataset?.description ?? "",
            saved: false,
          },
    [draftState, datasetKey, dataset?.name, dataset?.description],
  );
  const setNameDraft = (name: string) => setDraftState({ ...drafts, name });
  const setDescDraft = (description: string) => setDraftState({ ...drafts, description });
  const clearSaved = () => setDraftState({ ...drafts, saved: false });
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);

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

  const onSaveProfile = useCallback(async () => {
    if (!dataset) return;
    if (!drafts.name.trim()) {
      setProfileError(t("Name cannot be empty."));
      return;
    }
    setSavingProfile(true);
    setProfileError(null);
    try {
      const next = await updateDataset(dataset.id, {
        name: drafts.name.trim(),
        description: drafts.description,
      });
      onUpdated?.(next);
      setDraftState({ key: next.id, name: next.name, description: next.description, saved: true });
    } catch (err) {
      setProfileError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingProfile(false);
    }
  }, [dataset, drafts, onUpdated, t]);

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
          <dt className="text-[var(--muted-foreground)]">{t("Embedding model")}</dt>
          <dd className="text-[var(--foreground)]">{dataset.embeddingModel || "—"}</dd>
          <dt className="text-[var(--muted-foreground)]">{t("Documents")}</dt>
          <dd className="text-[var(--foreground)]">{dataset.documentCount}</dd>
          <dt className="text-[var(--muted-foreground)]">{t("Chunks")}</dt>
          <dd className="text-[var(--foreground)]">{dataset.chunkCount}</dd>
          <dt className="text-[var(--muted-foreground)]">{t("Tokens")}</dt>
          <dd className="text-[var(--foreground)]">
            {dataset.tokenCount > 0 ? formatBytes(dataset.tokenCount) : "—"}
          </dd>
          <dt className="text-[var(--muted-foreground)]">{t("Created")}</dt>
          <dd className="text-[var(--foreground)]">
            {formatEpochMillis(dataset.createdAt) || "—"}
          </dd>
        </dl>
      </section>

      <section className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4">
        <h3 className="mb-3 text-[13px] font-semibold text-[var(--foreground)]">
          {t("Rename & description")}
        </h3>
        <div className="space-y-2.5">
          <label className="block text-[11.5px] text-[var(--muted-foreground)]">
            {t("Name")}
            <input
              value={drafts.name}
              onChange={(event) => {
                setNameDraft(event.target.value);
                clearSaved();
              }}
              className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60"
            />
          </label>
          <label className="block text-[11.5px] text-[var(--muted-foreground)]">
            {t("Description")}
            <textarea
              value={drafts.description}
              rows={3}
              onChange={(event) => {
                setDescDraft(event.target.value);
                clearSaved();
              }}
              className="mt-1 w-full resize-y rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60"
            />
          </label>
          {profileError ? <p className="text-[12.5px] text-destructive">{profileError}</p> : null}
          {drafts.saved ? (
            <p className="text-[12.5px] text-emerald-700 dark:text-emerald-400">{t("Saved")}</p>
          ) : null}
          <Button
            variant="primary"
            size="sm"
            loading={savingProfile}
            disabled={!drafts.name.trim()}
            onClick={onSaveProfile}
          >
            {t("Save")}
          </Button>
        </div>
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
