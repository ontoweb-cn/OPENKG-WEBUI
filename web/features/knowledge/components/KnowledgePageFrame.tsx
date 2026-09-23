"use client";

import { type ReactNode } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Loader2, type LucideIcon } from "lucide-react";

/**
 * 知识中心页面框架（结构沿用 features/kag/components/KagPageFrame.tsx 的
 * /space 分节页约定；独立成域以免跨 feature 引用）。
 */

export function KnowledgePageHeader({
  icon: Icon,
  title,
  description,
  action,
  meta,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-col gap-4 border-b border-[var(--border)]/60 pb-5 md:flex-row md:items-end md:justify-between">
      <div className="flex items-start gap-3.5">
        <span
          aria-hidden
          className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-[var(--border)]/60 bg-[var(--card)] text-[var(--foreground)] shadow-sm"
        >
          <Icon size={16} strokeWidth={1.6} />
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-serif text-[19px] font-semibold leading-tight tracking-tight text-[var(--foreground)]">
              {title}
            </h1>
            {meta}
          </div>
          <p className="mt-1 max-w-xl text-[13px] leading-relaxed text-[var(--muted-foreground)]">
            {description}
          </p>
        </div>
      </div>
      {action ? <div className="shrink-0 self-start md:self-end">{action}</div> : null}
    </header>
  );
}

export function KnowledgeBackLink({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      className="group mb-5 inline-flex items-center gap-1.5 text-[13px] text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
    >
      <ArrowLeft
        size={15}
        strokeWidth={1.8}
        className="transition-transform group-hover:-translate-x-0.5"
      />
      {label}
    </Link>
  );
}

export function KnowledgePageBody({ children }: { children: ReactNode }) {
  return (
    <div className="h-full overflow-y-auto bg-[var(--background)] [scrollbar-gutter:stable]">
      <div className="mx-auto max-w-5xl px-8 py-8 pb-12">{children}</div>
    </div>
  );
}

/** 列表页的加载/出错占位视图。`action` 用于“未启用 → 去设置”引导。 */
export function KnowledgeStateView({
  loading,
  error,
  action,
}: {
  loading: boolean;
  error: string | null;
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center gap-2 text-sm text-[var(--muted-foreground)]">
        <Loader2 size={15} className="animate-spin" />
        {t("Loading...")}
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] px-5 py-8 text-center">
        <p className="text-[13.5px] font-medium text-[var(--foreground)]">
          {t("Knowledge center is unavailable")}
        </p>
        <p className="mx-auto mt-1.5 max-w-md break-words text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
          {error}
        </p>
        {action ? <div className="mt-4">{action}</div> : null}
      </div>
    );
  }
  return null;
}
