"use client";

/**
 * 会话级知识库勾选（Phase 1.5）：composer 的选择状态按 sessionId 缓存于
 * 模块级 Map——同一会话内的发送（ChatStateAdapter）与勾选（picker）共享；
 * 跨刷新由会话偏好（`knowledge_base_ids`）持久化，picker 挂载时回填缓存。
 */

import { requestJson } from "@/shared/api/client";

const cache = new Map<string, string[]>();

export function getSessionKnowledgeSelection(sessionId: string): string[] {
  return cache.get(sessionId) ?? [];
}

export function seedSessionKnowledgeSelection(
  sessionId: string,
  kbIds: unknown,
): void {
  if (cache.has(sessionId)) return;
  if (Array.isArray(kbIds)) cache.set(sessionId, kbIds.map(String));
}

export async function loadSessionKnowledgeSelection(
  sessionId: string,
): Promise<string[]> {
  const cached = cache.get(sessionId);
  if (cached) return cached;
  try {
    const payload = await requestJson<{ kb_ids?: unknown }>(
      `/api/sessions/${encodeURIComponent(sessionId)}/knowledge-selection`,
      { cache: "no-store", scope: "knowledge" },
    );
    const ids = Array.isArray(payload.kb_ids)
      ? payload.kb_ids.map(String)
      : [];
    cache.set(sessionId, ids);
    return ids;
  } catch {
    return [];
  }
}

export async function saveSessionKnowledgeSelection(
  sessionId: string,
  kbIds: string[],
): Promise<void> {
  cache.set(sessionId, kbIds);
  await requestJson(`/api/sessions/${encodeURIComponent(sessionId)}/knowledge-selection`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kb_ids: kbIds }),
    scope: "knowledge",
  });
}
