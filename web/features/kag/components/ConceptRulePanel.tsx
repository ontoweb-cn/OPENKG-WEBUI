"use client";

/**
 * C2 概念建模：CONCEPT_TYPE 展开区的「概念规则」面板。
 *
 * 浏览 + 增删两类规则：
 * - 分类规则（belongTo，taxonomy）——后端经 queryConcept 归一；
 * - 推理规则（leadTo，logical）——后端经 getReasoningConcept 归一。
 * 分类规则定义受 belongToReady 门禁（schema 中需存在 belongTo 属性——
 * 实体类型→该概念类型，否则 server 500 NPE，见计划文档 §5.2 前置核实）。
 */

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ListTree, Plus, Trash2 } from "lucide-react";

import { isApiError } from "@/shared/api/errors";
import {
  defineKagConceptRule,
  fetchKagConceptRules,
  removeKagConceptRule,
} from "../api";
import type { KagConceptRule, KagConceptRules, SpgTypeRow } from "../model";

interface ConceptRulePanelProps {
  projectId: string;
  /** 概念类型行（typeRow.key = 限定全名，如 m0ProbeLive.Topic）。 */
  typeRow: SpgTypeRow;
}

const MAX_DSL_CHARS = 400;

export function ConceptRulePanel({ projectId, typeRow }: ConceptRulePanelProps) {
  const { t } = useTranslation();
  const [rules, setRules] = useState<KagConceptRules | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [kind, setKind] = useState<"taxonomy" | "logical">("taxonomy");
  const [conceptName, setConceptName] = useState("");
  const [subjectName, setSubjectName] = useState("");
  const [predicate, setPredicate] = useState("leadTo");
  const [objectType, setObjectType] = useState(typeRow.key);
  const [objectName, setObjectName] = useState("");
  const [dsl, setDsl] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  // 评审 P2：共享取消守卫——概念类型切换/卸载时丢弃过期响应，防陈旧规则覆盖新类型
  const cancelledRef = useRef(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const next = await fetchKagConceptRules(projectId, typeRow.key);
      if (cancelledRef.current) return;
      setRules(next);
    } catch (cause) {
      if (cancelledRef.current) return;
      setRules(null);
      setError(
        cause instanceof Error ? cause.message : t("Concept rules could not be loaded"),
      );
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    cancelledRef.current = false;
    void load();
    return () => {
      cancelledRef.current = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, typeRow.key]);

  async function remove(rule: KagConceptRule) {
    setBusy(true);
    setFormError("");
    setNotice("");
    const body: {
      kind: "logical" | "taxonomy";
      concept_type_name: string;
      concept_name?: string;
      predicate_name?: string;
      object_concept_type_name?: string;
      object_concept_name?: string;
    } = { kind: rule.kind, concept_type_name: typeRow.key };
    if (rule.kind === "taxonomy") {
      body.concept_name = rule.conceptName;
    } else {
      body.concept_name = rule.subjectName;
      body.predicate_name = rule.predicate;
      body.object_concept_type_name = rule.objectType;
      body.object_concept_name = rule.objectName;
    }
    try {
      await removeKagConceptRule(projectId, body);
      setNotice(t("The rule was removed"));
      await load();
    } catch (cause) {
      setFormError(
        isApiError(cause) && cause.message
          ? cause.message
          : t("The rule could not be removed"),
      );
    } finally {
      setBusy(false);
    }
  }

  async function define() {
    setFormError("");
    setNotice("");
    if (kind === "taxonomy") {
      if (!conceptName.trim()) {
        setFormError(t("Concept name is required"));
        return;
      }
      if (!rules?.belongToReady) {
        setFormError(
          t(
            "This concept type has no belongTo relation. Add one via the entity type first.",
          ),
        );
        return;
      }
    } else if (!subjectName.trim() || !objectType.trim() || !objectName.trim()) {
      setFormError(
        t("Subject, object concept type and object concept name are required"),
      );
      return;
    }
    if (!dsl.trim()) {
      setFormError(t("Rule DSL is required"));
      return;
    }
    setBusy(true);
    try {
      await defineKagConceptRule(projectId, {
        kind,
        concept_type_name: typeRow.key,
        concept_name: kind === "taxonomy" ? conceptName.trim() : subjectName.trim(),
        predicate_name: kind === "logical" ? predicate.trim() || "leadTo" : undefined,
        object_concept_type_name: kind === "logical" ? objectType.trim() : undefined,
        object_concept_name: kind === "logical" ? objectName.trim() : undefined,
        dsl: dsl.trim(),
      });
      setNotice(t("The rule was defined"));
      setConceptName("");
      setSubjectName("");
      setObjectName("");
      setDsl("");
      await load();
    } catch (cause) {
      setFormError(
        isApiError(cause) && cause.message
          ? cause.message
          : t("The rule could not be defined"),
      );
    } finally {
      setBusy(false);
    }
  }

  const disabled = busy || !rules;

  return (
    <div className="mt-2 space-y-3 rounded-lg border border-[var(--border)]/50 bg-[var(--background)]/40 p-3">
      <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[var(--muted-foreground)]">
        <ListTree size={12} aria-hidden />
        {t("Concept rules")}
      </div>

      {loading ? (
        <p className="px-1 text-[11.5px] text-[var(--muted-foreground)]">
          {t("Loading")}
        </p>
      ) : error ? (
        <p className="px-1 text-[11.5px] text-red-600">{error}</p>
      ) : rules ? (
        <>
          {rules.taxonomy.length === 0 && rules.reasoning.length === 0 ? (
            <p className="px-1 text-[11.5px] text-[var(--muted-foreground)]">
              {t("No concept rules")}
            </p>
          ) : (
            <div className="space-y-2">
              {rules.taxonomy.length > 0 ? (
                <div className="space-y-1">
                  <p className="px-1 text-[11px] font-medium text-[var(--muted-foreground)]">
                    {t("Taxonomy rules (belongTo)")}
                  </p>
                  {rules.taxonomy.map((rule) => (
                    <RuleRow
                      key={`t-${rule.conceptName}`}
                      title={rule.conceptName}
                      dsl={rule.dsl}
                      busy={busy}
                      onRemove={() => void remove(rule)}
                      removeLabel={t("Remove rule")}
                    />
                  ))}
                </div>
              ) : null}
              {rules.reasoning.length > 0 ? (
                <div className="space-y-1">
                  <p className="px-1 text-[11px] font-medium text-[var(--muted-foreground)]">
                    {t("Reasoning rules (leadTo)")}
                  </p>
                  {rules.reasoning.map((rule) => (
                    <RuleRow
                      key={`l-${rule.subjectType}.${rule.subjectName}-${rule.predicate}-${rule.objectType}.${rule.objectName}`}
                      title={`${rule.subjectType}.${rule.subjectName} → ${rule.predicate} → ${rule.objectType}.${rule.objectName}`}
                      dsl={rule.dsl}
                      busy={busy}
                      onRemove={() => void remove(rule)}
                      removeLabel={t("Remove rule")}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          )}

          <div className="space-y-2 border-t border-[var(--border)]/50 pt-2">
            <div className="flex items-center gap-1">
              <Plus size={12} className="text-[var(--muted-foreground)]" aria-hidden />
              <span className="text-[11.5px] font-semibold text-[var(--muted-foreground)]">
                {t("Define a concept rule")}
              </span>
            </div>
            <div className="flex gap-1">
              {(
                [
                  ["taxonomy", t("Taxonomy (belongTo)")],
                  ["logical", t("Reasoning (leadTo)")],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  disabled={disabled}
                  onClick={() => setKind(value)}
                  className={`rounded-md px-2 py-1 text-[11px] font-medium transition-colors disabled:opacity-40 ${
                    kind === value
                      ? "bg-[var(--foreground)] text-[var(--background)]"
                      : "bg-[var(--muted)]/50 text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {kind === "taxonomy" ? (
              <div className="grid gap-2 sm:grid-cols-[1fr_2fr]">
                <input
                  value={conceptName}
                  onChange={(event) => setConceptName(event.target.value)}
                  disabled={disabled}
                  placeholder={t("Concept name")}
                  className="rounded-md border border-[var(--border)] bg-[var(--card)] px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-[var(--foreground)] disabled:opacity-40"
                />
                <input
                  value={dsl}
                  onChange={(event) => setDsl(event.target.value)}
                  disabled={disabled}
                  placeholder={t("Rule DSL")}
                  className="rounded-md border border-[var(--border)] bg-[var(--card)] px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-[var(--foreground)] disabled:opacity-40"
                />
              </div>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                <input
                  value={subjectName}
                  onChange={(event) => setSubjectName(event.target.value)}
                  disabled={disabled}
                  placeholder={t("Subject concept name")}
                  className="rounded-md border border-[var(--border)] bg-[var(--card)] px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-[var(--foreground)] disabled:opacity-40"
                />
                <input
                  value={predicate}
                  onChange={(event) => setPredicate(event.target.value)}
                  disabled={disabled}
                  placeholder={t("Predicate")}
                  className="rounded-md border border-[var(--border)] bg-[var(--card)] px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-[var(--foreground)] disabled:opacity-40"
                />
                <input
                  value={objectType}
                  onChange={(event) => setObjectType(event.target.value)}
                  disabled={disabled}
                  placeholder={t("Object concept type")}
                  className="rounded-md border border-[var(--border)] bg-[var(--card)] px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-[var(--foreground)] disabled:opacity-40"
                />
                <input
                  value={objectName}
                  onChange={(event) => setObjectName(event.target.value)}
                  disabled={disabled}
                  placeholder={t("Object concept name")}
                  className="rounded-md border border-[var(--border)] bg-[var(--card)] px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-[var(--foreground)] disabled:opacity-40"
                />
                <input
                  value={dsl}
                  onChange={(event) => setDsl(event.target.value)}
                  disabled={disabled}
                  placeholder={t("Rule DSL")}
                  className="rounded-md border border-[var(--border)] bg-[var(--card)] px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-[var(--foreground)] disabled:opacity-40 sm:col-span-2"
                />
              </div>
            )}

            {kind === "taxonomy" && rules && !rules.belongToReady ? (
              <p className="px-1 text-[11px] leading-relaxed text-amber-600 dark:text-amber-500">
                {t(
                  "This concept type has no belongTo relation. Add one via the entity type first.",
                )}
              </p>
            ) : null}
            {formError ? (
              <p className="px-1 text-[11.5px] text-red-600">{formError}</p>
            ) : null}
            {notice ? (
              <p className="px-1 text-[11.5px] text-emerald-600 dark:text-emerald-500">
                {notice}
              </p>
            ) : null}

            <button
              type="button"
              disabled={disabled}
              onClick={() => void define()}
              className="inline-flex items-center gap-1.5 rounded-md bg-[var(--foreground)] px-3 py-1.5 text-[11.5px] font-medium text-[var(--background)] transition-opacity hover:opacity-85 disabled:opacity-40"
            >
              {busy ? t("Submitting…") : t("Define rule")}
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}

function RuleRow({
  title,
  dsl,
  busy,
  onRemove,
  removeLabel,
}: {
  title: string;
  dsl: string;
  busy: boolean;
  onRemove: () => void;
  removeLabel: string;
}) {
  return (
    <div className="flex items-start gap-2 rounded-md bg-[var(--card)]/60 px-2 py-1.5">
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-[12px] text-[var(--foreground)]">
          {title}
        </span>
        {dsl ? (
          <span className="mt-0.5 block whitespace-pre-wrap break-all font-mono text-[10.5px] leading-relaxed text-[var(--muted-foreground)]">
            {dsl.length > MAX_DSL_CHARS ? `${dsl.slice(0, MAX_DSL_CHARS)}…` : dsl}
          </span>
        ) : null}
      </span>
      <button
        type="button"
        disabled={busy}
        onClick={onRemove}
        aria-label={removeLabel}
        className="shrink-0 rounded p-1 text-[var(--muted-foreground)] transition-colors hover:bg-red-500/10 hover:text-red-600 disabled:opacity-40"
      >
        <Trash2 size={13} aria-hidden />
      </button>
    </div>
  );
}
