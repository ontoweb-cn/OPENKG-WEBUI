"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { BookOpen, Database, Plus, Settings2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useKnowledgeStatus } from "@/hooks/useKnowledgeStatus";
import {
  createDataset,
  deleteDataset,
  fetchDatasets,
} from "../api";
import type { KnowledgeDataset, KnowledgeVisibility } from "../model";
import {
  KnowledgePageBody,
  KnowledgePageHeader,
  KnowledgeStateView,
} from "./KnowledgePageFrame";

/**
 * 知识中心列表页（Phase 1a T7）：知识库卡片列表 + 创建（D2：单引擎简化
 * 表单，无 connect/probe）+ 删除确认。加载/出错/未配置状态沿 KagStateView
 * 语义；未启用时给设置引导（验收 3）。
 *
 * 可见范围（评审 P1/D5）：徽标读 `visibility`（访问控制依据），不读 legacy
 * `permission`——后者不参与鉴权，曾导致"显示团队、实际私有"的错配。
 */

/**
 * 可见范围 → i18n 标签。
 *
 * 刻意不用 `t("Project")`：该键已被设置页用作**区块标题**（zh 为"项目资源"），
 * 复用会让徽标显示成"项目资源"（评审 P3-1）。这里改用 "Project only" 独立
 * 词条。同时每个键都是字面量，i18n 审计脚本（只解析 `t("…")`）才能看到它们——
 * 此前用 `t(VISIBILITY_LABELS[value])` 的查表形式对审计不可见。
 */
function visibilityLabel(t: (key: string) => string, value: KnowledgeVisibility): string {
  switch (value) {
    case "tenant":
      return t("Tenant");
    case "team":
      return t("Team");
    case "project":
      return t("Project only");
    default:
      return t("Private");
  }
}

export default function KnowledgeHomePage() {
  const { t } = useTranslation();
  const router = useRouter();
  const status = useKnowledgeStatus();

  const [datasets, setDatasets] = useState<KnowledgeDataset[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeDataset | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    setError(null);
    try {
      setDatasets(await fetchDatasets(signal));
    } catch (err) {
      if (signal?.aborted) return;
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    if (status && !status.enabled) return;
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [status, load]);

  const notEnabled = status != null && !status.enabled;
  const errorText = useMemo(() => {
    if (notEnabled) return t("Enable the knowledge center in Settings to get started.");
    if (status && status.identity_ok === false)
      return t("Your account is not linked to the Intellect service. Link it in Settings.");
    return error;
  }, [notEnabled, status, error, t]);

  const onCreate = async (form: { name: string; description: string; permission: "me" | "team" }) => {
    setBusy(true);
    try {
      await createDataset(form);
      setCreating(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async () => {
    if (!deleteTarget) return;
    setBusy(true);
    try {
      await deleteDataset(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setDeleteTarget(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <KnowledgePageBody>
      <KnowledgePageHeader
        icon={BookOpen}
        title={t("Knowledge center")}
        description={t("Manage knowledge bases backed by Intellect RAG.")}
        action={
          <Button
            variant="primary"
            icon={<Plus size={15} strokeWidth={2} />}
            onClick={() => setCreating(true)}
            disabled={notEnabled}
          >
            {t("New knowledge base")}
          </Button>
        }
      />
      <KnowledgeStateView
        loading={datasets == null && !notEnabled}
        error={errorText}
        action={
          notEnabled || status?.identity_ok === false ? (
            <Button variant="secondary" icon={<Settings2 size={14} />} onClick={() => router.push("/settings/knowledge")}>
              {t("Open Settings")}
            </Button>
          ) : undefined
        }
      />

      {datasets != null && datasets.length === 0 && !errorText ? (
        <div className="rounded-xl border border-dashed border-[var(--border)]/70 bg-[var(--card)]/50 px-6 py-12 text-center">
          <Database size={22} strokeWidth={1.5} className="mx-auto text-[var(--muted-foreground)]" />
          <p className="mt-3 text-[13.5px] font-medium text-[var(--foreground)]">
            {t("No knowledge bases yet")}
          </p>
          <p className="mt-1 text-[12.5px] text-[var(--muted-foreground)]">
            {t("Create a knowledge base, then upload documents to build its index.")}
          </p>
        </div>
      ) : null}

      {datasets != null && datasets.length > 0 ? (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {datasets.map((dataset) => (
            <li key={dataset.id}>
              <button
                type="button"
                onClick={() =>
                  router.push(`/knowledge-center/${encodeURIComponent(dataset.id)}`)
                }
                className="group w-full rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-4 text-left shadow-sm transition-colors hover:border-[var(--primary)]/40"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="min-w-0 truncate text-[14px] font-medium text-[var(--foreground)]">
                    {dataset.name}
                  </span>
                  <span className="shrink-0 rounded-md border border-[var(--border)]/60 px-1.5 py-0.5 text-[11px] text-[var(--muted-foreground)]">
                    {visibilityLabel(t, dataset.visibility)}
                  </span>
                </div>
                {dataset.description ? (
                  <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
                    {dataset.description}
                  </p>
                ) : null}
                <div
                  className="mt-3 flex items-center gap-3 text-[12px] text-[var(--muted-foreground)]"
                  onClick={(event) => event.stopPropagation()}
                >
                  <span>
                    {t("{{count}} documents", { count: dataset.documentCount })}
                  </span>
                  <span>{t("{{count}} chunks", { count: dataset.chunkCount })}</span>
                  <span
                    role="button"
                    tabIndex={0}
                    className="ml-auto cursor-pointer hover:text-[var(--foreground)]"
                    onClick={(event) => {
                      event.stopPropagation();
                      setDeleteTarget(dataset);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.stopPropagation();
                        setDeleteTarget(dataset);
                      }
                    }}
                    aria-label={t("Delete")}
                  >
                    {t("Delete")}
                  </span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {creating ? (
        <CreateDatasetDialog
          busy={busy}
          onCancel={() => setCreating(false)}
          onCreate={onCreate}
          createVisibility={status?.create_visibility ?? "private"}
        />
      ) : null}

      <ConfirmDialog
        open={deleteTarget != null}
        title={t("Delete knowledge base")}
        tone="danger"
        busy={busy}
        confirmLabel={t("Delete")}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={onDelete}
      >
        {t("\"{{name}}\" and its {{count}} records will be deleted. This cannot be undone.", {
          name: deleteTarget?.name ?? "",
          count: deleteTarget?.documentCount ?? 0,
        })}
      </ConfirmDialog>
    </KnowledgePageBody>
  );
}

function CreateDatasetDialog({
  busy,
  onCancel,
  onCreate,
  createVisibility,
}: {
  busy: boolean;
  onCancel: () => void;
  onCreate: (form: { name: string; description: string; permission: "me" | "team" }) => void;
  /** 新建库实际会得到的可见范围（由后端按身份归因头推导） */
  createVisibility: KnowledgeVisibility;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  // 上游按 Team/Project 头决定 visibility 并忽略请求体的 permission，所以这里
  // 不是"用户选择"而是"如实上报"：回传与之一致的 legacy 值即可（评审 P1-1）。
  const permission: "me" | "team" =
    createVisibility === "team" || createVisibility === "project" ? "team" : "me";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={t("New knowledge base")}
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="text-[15px] font-semibold text-[var(--foreground)]">
          {t("New knowledge base")}
        </h2>
        <label className="mt-4 block text-[12.5px] font-medium text-[var(--foreground)]">
          {t("Name")}
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            autoFocus
            className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60"
          />
        </label>
        <label className="mt-3 block text-[12.5px] font-medium text-[var(--foreground)]">
          {t("Description")}
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={2}
            className="mt-1 w-full resize-none rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] text-[var(--foreground)] outline-none focus:border-[var(--primary)]/60"
          />
        </label>
        <div className="mt-3">
          <span className="text-[12.5px] font-medium text-[var(--foreground)]">
            {t("Visibility")}
          </span>
          {/* 陈述而非选择：范围由身份（Team/Project 归因）决定，上游忽略请求体 */}
          <span className="ml-2 rounded-md border border-[var(--border)]/60 px-1.5 py-0.5 text-[11.5px] text-[var(--muted-foreground)]">
            {visibilityLabel(t, createVisibility)}
          </span>
          <p className="mt-1.5 text-[11.5px] leading-relaxed text-[var(--muted-foreground)]">
            {createVisibility === "private"
              ? t("Only you can see this knowledge base.")
              : t("Everyone in this scope can see the knowledge base.")}
          </p>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            {t("Cancel")}
          </Button>
          <Button
            variant="primary"
            loading={busy}
            disabled={!name.trim()}
            onClick={() => onCreate({ name: name.trim(), description: description.trim(), permission })}
          >
            {t("Create")}
          </Button>
        </div>
      </div>
    </div>
  );
}
