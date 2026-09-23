"use client";

import dynamic from "next/dynamic";

import { SettingsDomainGate } from "@/components/settings/SettingsDomainGate";

const KnowledgeCenterSection = dynamic(
  () => import("@/features/settings/sections/KnowledgeCenterSettingsSection"),
  { loading: () => <div className="min-h-40" aria-hidden="true" /> },
);

const DocumentParsingSection = dynamic(
  () => import("@/features/settings/sections/DocumentParsingSettingsSection"),
  { loading: () => <div className="min-h-80" aria-hidden="true" /> },
);

/**
 * 知识中心设置（决策 Q5：接管 /settings/knowledge，文档解析设置并入本页）。
 *
 * 上半部 = 知识中心连接（KnowledgeCenterSettingsSection，Phase 1a 新增）；
 * 下半部 = 原文档解析引擎设置（组件原样保留，零回归）。`SettingsDomainGate`
 * 沿用既有可见性检查。
 */
export default function KnowledgeSettingsRoute() {
  return (
    <SettingsDomainGate domain="knowledge">
      <div className="space-y-10">
        <KnowledgeCenterSection />
        <DocumentParsingSection />
      </div>
    </SettingsDomainGate>
  );
}
