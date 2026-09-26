"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/Button";

import {
  checkEmbeddingCompatibility,
  fetchEmbeddingModels,
  fetchKnowledgePreferences,
  putKnowledgePreferences,
  updateDataset,
} from "../api";
import {
  formatBytes,
  formatEpochMillis,
  type EmbeddingCheckResult,
  type EmbeddingModelOption,
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

  // P3 T13'：嵌入模型更换流程（13.0 下拉候选 + 13.1 兼容性检查 + 兼容才保存）
  const [changingModel, setChangingModel] = useState(false);
  const [modelOptions, setModelOptions] = useState<EmbeddingModelOption[] | null>(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<EmbeddingCheckResult | null>(null);
  const [modelFlowError, setModelError] = useState<string | null>(null);
  const [savingModel, setSavingModel] = useState(false);

  const startModelChange = useCallback(async () => {
    setChangingModel(true);
    setModelOptions(null);
    setCheckResult(null);
    setModelError(null);
    try {
      const options = await fetchEmbeddingModels();
      // 上游清单用裸模型名，dataset 行的 embd_id 带 @instance@provider 后缀——
      // 当前模型不在候选里时补插一项，保证预选与自检可用（阶段 2 live 修正）
      const current = dataset?.embeddingModel ?? "";
      if (current && !options.some((option) => option.name === current)) {
        options.unshift({ name: current, providerName: "current" });
      }
      setModelOptions(options);
      setSelectedModel(current);
    } catch (err) {
      setModelError(err instanceof Error ? err.message : String(err));
    }
  }, [dataset?.embeddingModel]);

  const runCheck = useCallback(async () => {
    if (!dataset || !selectedModel) return;
    setChecking(true);
    setCheckResult(null);
    setModelError(null);
    try {
      setCheckResult(await checkEmbeddingCompatibility(dataset.id, selectedModel));
    } catch (err) {
      setModelError(err instanceof Error ? err.message : String(err));
    } finally {
      setChecking(false);
    }
  }, [dataset, selectedModel]);

  const saveModel = useCallback(async () => {
    if (!dataset || !checkResult?.compatible) return;
    setSavingModel(true);
    setModelError(null);
    try {
      const next = await updateDataset(dataset.id, { embeddingModel: selectedModel });
      onUpdated?.(next);
      setChangingModel(false);
    } catch (err) {
      setModelError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingModel(false);
    }
  }, [dataset, checkResult?.compatible, selectedModel, onUpdated]);

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
          <dd className="flex items-center gap-2 text-[var(--foreground)]">
            <span className="min-w-0 break-all">{dataset.embeddingModel || "—"}</span>
            {!changingModel ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void startModelChange()}
              >
                {t("Change")}
              </Button>
            ) : null}
          </dd>
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

        {changingModel ? (
          <div className="mt-3 space-y-2.5 border-t border-[var(--border)]/50 pt-3">
            {modelOptions == null ? (
              <p className="text-[12px] text-[var(--muted-foreground)]">{t("Loading...")}</p>
            ) : modelOptions.length === 0 ? (
              <p className="text-[12px] text-[var(--muted-foreground)]">
                {t("No embedding models are configured on the RAG server.")}
              </p>
            ) : (
              <>
                <label className="block text-[11.5px] text-[var(--muted-foreground)]">
                  {t("New embedding model")}
                  <select
                    value={selectedModel}
                    onChange={(event) => {
                      setSelectedModel(event.target.value);
                      setCheckResult(null);
                    }}
                    className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[12.5px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60"
                  >
                    {modelOptions.map((option) => (
                      <option key={option.name} value={option.name}>
                        {option.name}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="flex items-center gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={checking}
                    disabled={!selectedModel}
                    onClick={() => void runCheck()}
                  >
                    {t("Check compatibility")}
                  </Button>
                  <Button
                    variant="primary"
                    size="sm"
                    loading={savingModel}
                    disabled={
                      !checkResult?.compatible ||
                      !selectedModel ||
                      selectedModel === dataset.embeddingModel
                    }
                    onClick={() => void saveModel()}
                  >
                    {t("Save")}
                  </Button>
                </div>
                {checkResult ? (
                  <div
                    className={`rounded-lg border px-3 py-2 text-[12px] ${
                      checkResult.compatible
                        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                        : "border-destructive/30 bg-destructive/10 text-destructive"
                    }`}
                  >
                    <p className="font-medium">
                      {checkResult.compatible
                        ? t("Compatible — safe to switch.")
                        : t("Not compatible with the stored vectors.")}
                    </p>
                    <p className="mt-0.5 text-[11.5px] opacity-80">
                      {t("avg {{avg}} · min {{min}} · max {{max}} · {{valid}}/{{sampled}} samples valid", {
                        avg: checkResult.avgCosSim.toFixed(3),
                        min: checkResult.minCosSim.toFixed(3),
                        max: checkResult.maxCosSim.toFixed(3),
                        valid: checkResult.valid,
                        sampled: checkResult.sampled,
                      })}
                    </p>
                    {checkResult.reason ? (
                      <p className="mt-1 text-[11.5px] opacity-90">{checkResult.reason}</p>
                    ) : null}
                    {checkResult.compatible ? (
                      <p className="mt-1 text-[11.5px] opacity-90">
                        {t("Existing chunk vectors are not rebuilt automatically — re-parse documents after switching.")}
                      </p>
                    ) : null}
                  </div>
                ) : null}
                {modelFlowError ? (
                  <p className="text-[12px] text-destructive">{modelFlowError}</p>
                ) : null}
                <p className="text-[11px] text-[var(--muted-foreground)]">
                  {t("The compatibility check re-embeds {{count}} sampled chunks on the server.", { count: 5 })}
                </p>
              </>
            )}
          </div>
        ) : null}
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
