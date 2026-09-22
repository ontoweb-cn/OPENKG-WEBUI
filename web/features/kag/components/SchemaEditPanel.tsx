"use client";

/**
 * M3.5 Schema 编辑面板：类型树展开区的关系增删表单。
 *
 * 编辑契约（后端 /schema/alter）：spg_type 原样回传读模型（wire 转换在
 * 服务端），增删以意图列表表达——删除不是从数组剔除（服务端要求元素级
 * DELETE 操作），由后端组装。
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Cog, Languages, Link2, Plus, Trash2 } from "lucide-react";

import { isApiError } from "@/shared/api/errors";
import { alterKagProjectSchema } from "../api";
import { parseRelations, ownProperties, type SpgTypeRow } from "../model";

interface SchemaEditPanelProps {
  projectId: string;
  typeRow: SpgTypeRow;
  /** schema 内全部类型（object type 下拉；排除自身）。 */
  allTypes: SpgTypeRow[];
  onAltered: () => void;
}

export function SchemaEditPanel({
  projectId,
  typeRow,
  allTypes,
  onAltered,
}: SchemaEditPanelProps) {
  const { t } = useTranslation();
  const [deleting, setDeleting] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [nameZh, setNameZh] = useState("");
  const [objectTypeName, setObjectTypeName] = useState("");
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // —— 属性区（A-S1）：新增/删除自有属性 ——
  const [markPropDelete, setMarkPropDelete] = useState<string[]>([]);
  const [propName, setPropName] = useState("");
  const [propNameZh, setPropNameZh] = useState("");
  const [propType, setPropType] = useState("");
  const [propConstraint, setPropConstraint] = useState("");
  const basicTypes = allTypes
    .filter((row) => row.kind === "basic")
    .sort((a, b) => a.name.localeCompare(b.name));

  const relations = parseRelations(typeRow.raw.relations);
  const ownRelations = relations.filter((rel) => !rel.inherited);
  // —— 中英映射（A-S2）：名称/描述 → 纯 UPDATE 覆写 ——
  // 显式构建 Record（避免 Object.fromEntries 在严格 tsconfig 下退化为 any）。
  function zhMap(pairs: [string, string][]): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [key, value] of pairs) out[key] = value;
    return out;
  }
  const [labelOpen, setLabelOpen] = useState(false);
  const [typeNameZh, setTypeNameZh] = useState(typeRow.nameZh);
  const [typeDesc, setTypeDesc] = useState(typeRow.desc);
  const [propZhMap, setPropZhMap] = useState<Record<string, string>>(() =>
    zhMap(ownProperties(typeRow).map((p): [string, string] => [p.name, p.nameZh])),
  );
  const [relZhMap, setRelZhMap] = useState<Record<string, string>>(() =>
    zhMap(ownRelations.map((r): [string, string] => [r.name, r.nameZh])),
  );
  const [savingLabels, setSavingLabels] = useState(false);
  const candidates = allTypes.filter(
    (row) => row.kind !== "basic" && row.key !== typeRow.key,
  );

  async function submit(add: boolean) {
    setError("");
    if (add) {
      if (!name.trim() || !objectTypeName) {
        setError(t("Relation name and object type are required"));
        return;
      }
    } else if (deleting.length === 0) {
      return;
    }
    setBusy(true);
    try {
      await alterKagProjectSchema(projectId, {
        spg_type: typeRow.raw,
        add_relations: add
          ? [
              {
                name: name.trim(),
                name_zh: nameZh.trim(),
                desc: desc.trim(),
                object_type_name: objectTypeName,
              },
            ]
          : undefined,
        delete_relations: add ? undefined : deleting,
      });
      setDeleting([]);
      setName("");
      setNameZh("");
      setDesc("");
      onAltered();
    } catch (err) {
      setError(
        isApiError(err) && err.message
          ? err.message
          : t("The schema change was rejected"),
      );
    } finally {
      setBusy(false);
    }
  }

  /** 属性相关：自有属性列表用 typeRow.properties（inherited 已剔除）。 */
  const ownProps = typeRow.properties.filter((property) => !property.inherited);

  async function submitProperty(add: boolean) {
    setError("");
    if (add) {
      if (!propName.trim() || !propType) {
        setError(t("Property name and type are required"));
        return;
      }
    } else if (markPropDelete.length === 0) {
      return;
    }
    setBusy(true);
    try {
      await alterKagProjectSchema(projectId, {
        spg_type: typeRow.raw,
        add_properties: add
          ? [
              {
                name: propName.trim(),
                name_zh: propNameZh.trim(),
                object_type_name: propType,
                constraint: propConstraint || undefined,
              },
            ]
          : undefined,
        delete_properties: add ? undefined : markPropDelete,
      });
      setMarkPropDelete([]);
      setPropName("");
      setPropNameZh("");
      setPropConstraint("");
      onAltered();
    } catch (err) {
      setError(
        isApiError(err) && err.message
          ? err.message
          : t("The schema change was rejected"),
      );
    } finally {
      setBusy(false);
    }
  }

  /** A-S2：把名称/描述写回 spg_type 读模型，走纯 UPDATE 覆写提交。 */
  async function saveLabels() {
    const updated = structuredClone(typeRow.raw) as {
      basicInfo?: Record<string, unknown>;
      properties?: { basicInfo?: { name?: { name?: string }; nameZh?: string } }[];
      relations?: { basicInfo?: { name?: { name?: string }; nameZh?: string } }[];
    };
    if (!updated.basicInfo) updated.basicInfo = {};
    updated.basicInfo.nameZh = typeNameZh;
    updated.basicInfo.desc = typeDesc;
    for (const prop of updated.properties ?? []) {
      const nm = prop.basicInfo?.name?.name;
      if (nm && propZhMap[nm] !== undefined && prop.basicInfo) {
        prop.basicInfo.nameZh = propZhMap[nm];
      }
    }
    for (const rel of updated.relations ?? []) {
      const nm = rel.basicInfo?.name?.name;
      if (nm && relZhMap[nm] !== undefined && rel.basicInfo) {
        rel.basicInfo.nameZh = relZhMap[nm];
      }
    }
    setSavingLabels(true);
    setError("");
    try {
      await alterKagProjectSchema(projectId, { spg_type: updated });
      setLabelOpen(false);
      onAltered();
    } catch (err) {
      setError(
        isApiError(err) && err.message
          ? err.message
          : t("The schema change was rejected"),
      );
    } finally {
      setSavingLabels(false);
    }
  }

  return (
    <div className="mt-2 space-y-3 rounded-lg border border-[var(--border)]/50 bg-[var(--background)]/40 p-3">
      {/* —— A-S2 中英映射：名称/描述编辑 → 纯 UPDATE 覆写 —— */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[var(--muted-foreground)]">
          <Languages size={12} aria-hidden />
          {t("Names (zh mapping)")}
        </div>
        <button
          type="button"
          onClick={() => setLabelOpen((value) => !value)}
          className="rounded-md border border-[var(--border)]/60 px-2.5 py-1 text-[11px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/40"
        >
          {labelOpen ? t("Collapse") : t("Edit labels")}
        </button>
      </div>
      {labelOpen ? (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <input
              value={typeNameZh}
              disabled={savingLabels}
              onChange={(e) => setTypeNameZh(e.target.value)}
              placeholder={t("Type name (Chinese)")}
              className="rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
            />
            <input
              value={typeDesc}
              disabled={savingLabels}
              onChange={(e) => setTypeDesc(e.target.value)}
              placeholder={t("Description (optional)")}
              className="rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
            />
          </div>
          {ownProperties(typeRow).length > 0 ? (
            <div className="space-y-1">
              <div className="text-[10.5px] font-medium text-[var(--muted-foreground)]">
                {t("Properties")}
              </div>
              {ownProperties(typeRow).map((property) => (
                <div key={property.name} className="flex items-center gap-2">
                  <span className="w-1/3 shrink-0 truncate font-mono text-[11.5px] text-[var(--muted-foreground)]">
                    {property.name}
                  </span>
                  <input
                    value={propZhMap[property.name] ?? ""}
                    disabled={savingLabels}
                    onChange={(e) =>
                      setPropZhMap((prev) => ({
                        ...prev,
                        [property.name]: e.target.value,
                      }))
                    }
                    className="min-w-0 flex-1 rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2 py-1 text-[12px] text-[var(--foreground)] outline-none focus:border-[var(--foreground)]/30"
                  />
                </div>
              ))}
            </div>
          ) : null}
          {ownRelations.length > 0 ? (
            <div className="space-y-1">
              <div className="text-[10.5px] font-medium text-[var(--muted-foreground)]">
                {t("Relations")}
              </div>
              {ownRelations.map((rel) => (
                <div key={rel.name} className="flex items-center gap-2">
                  <span className="w-1/3 shrink-0 truncate font-mono text-[11.5px] text-[var(--muted-foreground)]">
                    {rel.name}
                  </span>
                  <input
                    value={relZhMap[rel.name] ?? ""}
                    disabled={savingLabels}
                    onChange={(e) =>
                      setRelZhMap((prev) => ({
                        ...prev,
                        [rel.name]: e.target.value,
                      }))
                    }
                    className="min-w-0 flex-1 rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2 py-1 text-[12px] text-[var(--foreground)] outline-none focus:border-[var(--foreground)]/30"
                  />
                </div>
              ))}
            </div>
          ) : null}
          <button
            type="button"
            disabled={savingLabels}
            onClick={() => void saveLabels()}
            className="w-full rounded-md bg-[var(--primary)] px-3 py-1.5 text-[12px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {savingLabels ? t("Submitting…") : t("Save labels")}
          </button>
        </div>
      ) : null}
      <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[var(--muted-foreground)]">
        <Link2 size={12} aria-hidden />
        {t("Relations")}
      </div>
      {relations.length === 0 ? (
        <p className="px-1 text-[11.5px] text-[var(--muted-foreground)]">
          {t("No relations")}
        </p>
      ) : (
        <div className="space-y-1">
          {relations.map((rel) => {
            const marked = deleting.includes(rel.name);
            return (
              <div
                key={rel.name}
                className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-[12px] transition-colors ${
                  marked
                    ? "bg-red-500/10 opacity-60"
                    : "bg-[var(--card)]/60"
                }`}
              >
                <span className="min-w-0 flex-1 truncate font-mono text-[var(--foreground)]">
                  {rel.name}
                </span>
                <span className="shrink-0 truncate font-mono text-[11px] text-[var(--muted-foreground)]">
                  {rel.objectType}
                </span>
                {rel.inherited ? null : (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      setDeleting((prev) =>
                        marked
                          ? prev.filter((n) => n !== rel.name)
                          : [...prev, rel.name],
                      )
                    }
                    aria-label={t("Mark relation for deletion")}
                    className="shrink-0 rounded p-1 text-[var(--muted-foreground)] transition-colors hover:bg-red-500/10 hover:text-red-600 disabled:opacity-40"
                  >
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
      {deleting.length > 0 ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => submit(false)}
          className="w-full rounded-md bg-red-600 px-3 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
        >
          {busy
            ? t("Submitting…")
            : t("Delete {{count}} relation(s)", { count: deleting.length })}
        </button>
      ) : null}

      <div className="space-y-2 border-t border-[var(--border)]/40 pt-3">
        <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[var(--muted-foreground)]">
          <Plus size={12} aria-hidden />
          {t("Add a relation")}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <input
            value={name}
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("Relation name (e.g. workFor)")}
            className="col-span-2 rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 font-mono text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
          />
          <input
            value={nameZh}
            disabled={busy}
            onChange={(e) => setNameZh(e.target.value)}
            placeholder={t("Display name (Chinese)")}
            className="rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
          />
          <select
            value={objectTypeName}
            disabled={busy}
            onChange={(e) => setObjectTypeName(e.target.value)}
            className="rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none focus:border-[var(--foreground)]/30"
          >
            <option value="">{t("Object type")}</option>
            {candidates.map((row) => (
              <option key={row.key} value={row.name}>
                {row.name}
              </option>
            ))}
          </select>
          <input
            value={desc}
            disabled={busy}
            onChange={(e) => setDesc(e.target.value)}
            placeholder={t("Description (optional)")}
            className="col-span-2 rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
          />
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => submit(true)}
          className="w-full rounded-md bg-[var(--primary)] px-3 py-1.5 text-[12px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {busy ? t("Submitting…") : t("Add relation")}
        </button>
      </div>

      {/* —— 属性区（A-S1）—— */}
      <div className="space-y-2 border-t border-[var(--border)]/40 pt-3">
        <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[var(--muted-foreground)]">
          <Cog size={12} aria-hidden />
          {t("Properties")}
        </div>
        {ownProps.length === 0 ? (
          <p className="px-1 text-[11.5px] text-[var(--muted-foreground)]">
            {t("No own properties")}
          </p>
        ) : (
          <div className="space-y-1">
            {ownProps.map((property) => {
              const marked = markPropDelete.includes(property.name);
              return (
                <div
                  key={property.name}
                  className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-[12px] transition-colors ${
                    marked
                      ? "bg-red-500/10 opacity-60"
                      : "bg-[var(--card)]/60"
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-[var(--foreground)]">
                    {property.name}
                  </span>
                  <span className="shrink-0 truncate font-mono text-[11px] text-[var(--muted-foreground)]">
                    {property.objectType}
                  </span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      setMarkPropDelete((prev) =>
                        marked
                          ? prev.filter((n) => n !== property.name)
                          : [...prev, property.name],
                      )
                    }
                    aria-label={t("Mark property for deletion")}
                    className="shrink-0 rounded p-1 text-[var(--muted-foreground)] transition-colors hover:bg-red-500/10 hover:text-red-600 disabled:opacity-40"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              );
            })}
          </div>
        )}
        {markPropDelete.length > 0 ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => submitProperty(false)}
            className="w-full rounded-md bg-red-600 px-3 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
          >
            {busy
              ? t("Submitting…")
              : t("Delete {{count}} property(s)", { count: markPropDelete.length })}
          </button>
        ) : null}

        <div className="space-y-2 border-t border-[var(--border)]/40 pt-2">
          <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[var(--muted-foreground)]">
            <Plus size={12} aria-hidden />
            {t("Add a property")}
          </div>
          <input
            value={propName}
            disabled={busy}
            onChange={(e) => setPropName(e.target.value)}
            placeholder={t("Property name (e.g. age)")}
            className="w-full rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 font-mono text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
          />
          <input
            value={propNameZh}
            disabled={busy}
            onChange={(e) => setPropNameZh(e.target.value)}
            placeholder={t("Display name (Chinese, required)")}
            className="w-full rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
          />
          <div className="grid grid-cols-2 gap-2">
            <select
              value={propType}
              disabled={busy}
              onChange={(e) => setPropType(e.target.value)}
              className="rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none focus:border-[var(--foreground)]/30"
            >
              <option value="">{t("Type")}</option>
              {basicTypes.map((row) => (
                <option key={row.key} value={row.name}>
                  {row.name}
                </option>
              ))}
            </select>
            <select
              value={propConstraint}
              disabled={busy}
              onChange={(e) => setPropConstraint(e.target.value)}
              className="rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none focus:border-[var(--foreground)]/30"
            >
              <option value="">{t("No constraint")}</option>
              <option value="NOT_NULL">{t("NOT_NULL")}</option>
            </select>
          </div>
          <button
            type="button"
            disabled={busy}
            onClick={() => submitProperty(true)}
            className="w-full rounded-md bg-[var(--primary)] px-3 py-1.5 text-[12px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {busy ? t("Submitting…") : t("Add property")}
          </button>
        </div>
      </div>

      {error ? (
        <p className="rounded-md bg-red-500/10 px-2.5 py-1.5 text-[11.5px] text-red-600 dark:text-red-400">
          {error}
        </p>
      ) : null}
    </div>
  );
}
