"use client";

/**
 * 知识中心可用性状态（docs/knowledge-center-port-design.md 验收 3：
 * 服务未启用时导航入口隐藏，避免失效入口）。
 *
 * 模块级缓存：一次会话只打一次 /api/knowledge；设置页保存后调用
 * refreshKnowledgeStatus() 失效缓存。
 */

import { useEffect, useState } from "react";

import { requestJson } from "@/shared/api/client";

export interface KnowledgeStatus {
  enabled: boolean;
  identity_ok: boolean | null;
}

let cache: KnowledgeStatus | null = null;
let inflight: Promise<KnowledgeStatus> | null = null;

async function loadStatus(): Promise<KnowledgeStatus> {
  try {
    return await requestJson<KnowledgeStatus>("/api/knowledge-center", {
      cache: "no-store",
      scope: "knowledge",
    });
  } catch {
    // 401 会由 apiFetch 重定向；其余失败一律视为不可用（入口隐藏）
    return { enabled: false, identity_ok: false };
  }
}

export function refreshKnowledgeStatus(): Promise<KnowledgeStatus> {
  cache = null;
  inflight = null;
  return loadStatus().then((status) => {
    cache = status;
    return status;
  });
}

/** null = 尚未探明（加载期，导航入口先隐藏）；否则为最近一次状态。 */
export function useKnowledgeStatus(): KnowledgeStatus | null {
  const [status, setStatus] = useState<KnowledgeStatus | null>(cache);

  useEffect(() => {
    if (cache) return;
    if (!inflight) inflight = loadStatus();
    inflight.then((next) => {
      cache = next;
      setStatus(next);
    });
  }, []);

  return status;
}
