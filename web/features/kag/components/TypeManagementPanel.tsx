"use client";

/**
 * A-S1 类型结构管理：新建实体类型 + 删除实体类型。
 *
 * 后端 /schema/alter 的 add_types/delete_types（A-S0 实测 wire）。由于该端点
 * 需一个 ``spg_type``（UPDATE 锚，无变化即无害），这里用 schema 中任一业务
 * 类型读模型作锚。名称规则（A-S0 §1）：类型名大写开头、无下划线；删除有子
 * 类型会被后端拒绝（server 会留孤儿），错误信息透传展示。
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Boxes, Plus, Trash2 } from "lucide-react";

import { isApiError } from "@/shared/api/errors";
import { alterKagProjectSchema } from "../api";
import type { SpgTypeRow } from "../model";

export function TypeManagementPanel({
  projectId,
  rows,
  onChanged,
}: {
  projectId: string;
  rows: SpgTypeRow[];
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  // 业务类型（可作锚/父/可删）；basic/standard 结构由系统固定，排除。
  const business = rows.filter(
    (row) => row.kind !== "basic" && row.kind !== "standard",
  );
  const entities = business.filter((row) => row.kind === "entity");

  const [name, setName] = useState("");
  const [nameZh, setNameZh] = useState("");
  const [parent, setParent] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "err" | "ok"; text: string } | null>(null);

  /** 提交所需的 UPDATE 锚（避开被删集合；全被除外则无法锚 → null）。 */
  function anchorRaw(except: string[]): Record<string, unknown> | null {
    const exclude = new Set(except);
    const row = business.find((candidate) => !exclude.has(candidate.name));
    return row?.raw ?? null;
  }

  async function createType() {
    const trimmed = name.trim();
    if (!trimmed || !parent) {
      setMessage({ tone: "err", text: t("Type name and parent are required") });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const anchor = anchorRaw([]);
      if (!anchor) throw new Error("no anchor type");
      await alterKagProjectSchema(projectId, {
        spg_type: anchor,
        add_types: [{ name: trimmed, name_zh: nameZh.trim(), parent_name: parent }],
      });
      setName("");
      setNameZh("");
      setMessage({ tone: "ok", text: t("Entity type created") });
      onChanged();
    } catch (err) {
      setMessage({
        tone: "err",
        text: isApiError(err) && err.message ? err.message : t("The schema change was rejected"),
      });
    } finally {
      setBusy(false);
    }
  }

  async function deleteType(typeName: string) {
    setBusy(true);
    setMessage(null);
    try {
      const anchor = anchorRaw([typeName]);
      if (!anchor) throw new Error("no anchor type");
      await alterKagProjectSchema(projectId, {
        spg_type: anchor,
        delete_types: [typeName],
      });
      setMessage({ tone: "ok", text: t("Entity type deleted") });
      onChanged();
    } catch (err) {
      setMessage({
        tone: "err",
        text: isApiError(err) && err.message ? err.message : t("The schema change was rejected"),
      });
    } finally {
      setBusy(false);
    }
  }

  if (business.length === 0) return null;

  return (
    <div className="mb-4 rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-3">
      <div className="flex items-center gap-1.5 text-[12px] font-semibold text-[var(--muted-foreground)]">
        <Boxes size={13} aria-hidden />
        {t("Entity type management")}
      </div>

      <div className="mt-2 space-y-2">
        <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[var(--muted-foreground)]">
          <Plus size={12} aria-hidden />
          {t("Add an entity type")}
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            value={name}
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("Type name (uppercase, e.g. Product)")}
            className="min-w-0 flex-1 rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 font-mono text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
          />
          <input
            value={nameZh}
            disabled={busy}
            onChange={(e) => setNameZh(e.target.value)}
            placeholder={t("Display name (Chinese)")}
            className="rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
          />
          <select
            value={parent}
            disabled={busy}
            onChange={(e) => setParent(e.target.value)}
            className="rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none focus:border-[var(--foreground)]/30"
          >
            <option value="">{t("Parent type")}</option>
            {entities.map((row) => (
              <option key={row.key} value={row.name}>
                {row.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={busy}
            onClick={() => void createType()}
            className="inline-flex items-center gap-1.5 rounded-md bg-[var(--primary)] px-3 py-1.5 text-[12px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            <Plus size={12} aria-hidden />
            {busy ? t("Submitting…") : t("Add")}
          </button>
        </div>

        {entities.length > 0 ? (
          <div className="space-y-1">
            <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[var(--muted-foreground)]">
              <Trash2 size={12} aria-hidden />
              {t("Delete an entity type")}
            </div>
            {entities.map((row) => (
              <div
                key={row.key}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[12px] bg-[var(--card)]/60"
              >
                <span className="min-w-0 flex-1 truncate font-mono text-[var(--foreground)]">
                  {row.name}
                </span>
                <span className="shrink-0 truncate font-mono text-[11px] text-[var(--muted-foreground)]">
                  {row.nameZh}
                </span>
                <button
                  type="button"
                  disabled={busy || !business.some((b) => b.name !== row.name)}
                  onClick={() => void deleteType(row.name)}
                  aria-label={t("Delete entity type")}
                  className="shrink-0 rounded p-1 text-[var(--muted-foreground)] transition-colors hover:bg-red-500/10 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      {message ? (
        <p
          className={`mt-2 rounded-md px-2.5 py-1.5 text-[11.5px] ${
            message.tone === "ok"
              ? "bg-green-500/10 text-green-700 dark:text-green-400"
              : "bg-red-500/10 text-red-600 dark:text-red-400"
          }`}
        >
          {message.text}
        </p>
      ) : null}
    </div>
  );
}