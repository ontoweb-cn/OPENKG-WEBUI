"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import Link from "next/link";

import {
  SettingRow,
  SettingSection,
  SettingsPageHeader,
  inputClass,
} from "@/components/settings/shared";
import { Toggle } from "@/components/settings/Toggle";
import { Button } from "@/components/ui/Button";
import { requestJson } from "@/shared/api/client";
import { refreshKnowledgeStatus } from "@/hooks/useKnowledgeStatus";
import {
  fetchKnowledgeStatus,
  type KnowledgeStatus,
} from "@/features/knowledge/api";

/**
 * `/settings/knowledge` 的知识中心区块（Phase 1a T8；决策 Q5：接管该路由，
 * 文档解析设置并入同页下方）。
 *
 * 即改即存模式（沿 DocumentParsingSettingsSection，不走草稿/全局 Apply）。
 * api_key write-only：GET 不回显，PUT 省略即保留（tri-state，kag 先例）。
 * 身份不在此配置——与 agent-loop 共用身份源（P1-1），此处只展示链接状态并
 * 引导到 /settings/agent-loop。
 */

interface KnowledgeSettingsDraft {
  enabled: boolean;
  base_url: string;
  api_key_set: boolean;
}

export default function KnowledgeCenterSettingsSection() {
  const { t } = useTranslation();
  const [loaded, setLoaded] = useState<KnowledgeSettingsDraft | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedFlash, setSavedFlash] = useState(false);
  const [status, setStatus] = useState<KnowledgeStatus | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const payload = await requestJson<Record<string, unknown>>(
        "/api/settings/knowledge",
        { cache: "no-store", scope: "settings" },
      );
      setLoaded({
        enabled: payload.enabled === true,
        base_url: typeof payload.base_url === "string" ? payload.base_url : "",
        api_key_set: payload.api_key_set === true,
      });
      setApiKey("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    load();
    fetchKnowledgeStatus()
      .then(setStatus)
      .catch(() => setStatus({ enabled: false, identity_ok: false }));
  }, [load]);

  const save = async (next: KnowledgeSettingsDraft) => {
    setSaving(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        enabled: next.enabled,
        base_url: next.base_url,
      };
      // tri-state：留空 = 保留已存值
      if (apiKey.trim()) body.api_key = apiKey.trim();
      await requestJson<unknown>("/api/settings/knowledge", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        scope: "settings",
      });
      setLoaded(next);
      setApiKey("");
      setSavedFlash(true);
      window.setTimeout(() => setSavedFlash(false), 2000);
      refreshKnowledgeStatus()
        .then(setStatus)
        .catch(() => undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const identityOk = status?.identity_ok === true;

  return (
    <div>
      <SettingsPageHeader
        title={t("Knowledge center")}
        description={t(
          "Connect OPENKG-WebUI to Intellect RAG for knowledge base management.",
        )}
      />
      {loaded ? (
        <>
          <SettingSection
            title={t("Intellect RAG connection")}
            description={t(
              "The API key is the same one configured for the Intellect agent service (INTELLECT_RAG_API_KEY).",
            )}
          >
            <SettingRow
              title={t("Enable knowledge center")}
              description={t(
                "Shows the Knowledge entry in the workspace navigation.",
              )}
              control={
                <Toggle
                  checked={loaded.enabled}
                  disabled={saving}
                  onChange={(next) => save({ ...loaded, enabled: next })}
                />
              }
            />
            <SettingRow
              title={t("Intellect RAG server URL")}
              description={t("Base URL of the intellect-rag-app API, e.g. http://127.0.0.1:9380")}
              control={
                <input
                  value={loaded.base_url}
                  disabled={saving}
                  placeholder="http://127.0.0.1:9380"
                  onChange={(event) =>
                    setLoaded({ ...loaded, base_url: event.target.value })
                  }
                  onBlur={() => save(loaded)}
                  className={`${inputClass} w-72`}
                />
              }
            />
            <SettingRow
              title={t("API key")}
              description={
                loaded.api_key_set
                  ? t("A key is saved. Leave blank to keep it.")
                  : t("Bearer token used by this deployment to call Intellect RAG.")
              }
              control={
                <input
                  type="password"
                  value={apiKey}
                  disabled={saving}
                  placeholder={loaded.api_key_set ? "••••••••" : ""}
                  onChange={(event) => setApiKey(event.target.value)}
                  onBlur={() => {
                    if (apiKey.trim()) save(loaded);
                  }}
                  className={`${inputClass} w-72`}
                />
              }
            />
            <SettingRow
              title={t("Identity link")}
              description={
                identityOk
                  ? t("Linked. Retrieval runs under your Intellect identity.")
                  : t("Link your Intellect account so retrieval runs under your identity.")
              }
              control={
                identityOk ? (
                  <span className="text-[12.5px] text-emerald-600 dark:text-emerald-400">
                    {t("Linked")}
                  </span>
                ) : (
                  <Link
                    href="/settings/agent-loop"
                    className="text-[12.5px] underline underline-offset-2"
                  >
                    {t("Open agent-loop settings")}
                  </Link>
                )
              }
            />
          </SettingSection>
          <div className="flex items-center gap-3">
            <Button
              variant="primary"
              loading={saving}
              onClick={() => save(loaded)}
            >
              {t("Save")}
            </Button>
            {savedFlash ? (
              <span className="text-[12.5px] text-[var(--muted-foreground)]">
                {t("Saved")}
              </span>
            ) : null}
            {error ? (
              <span className="text-[12.5px] text-destructive">{error}</span>
            ) : null}
          </div>
        </>
      ) : (
        <p className="py-6 text-[13px] text-[var(--muted-foreground)]">
          {error ?? t("Loading...")}
        </p>
      )}
    </div>
  );
}
