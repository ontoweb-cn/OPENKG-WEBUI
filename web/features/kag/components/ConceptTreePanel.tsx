"use client";

/**
 * B.4 完整概念树浏览：CONCEPT_TYPE 展开区的「概念树」面板。
 *
 * 懒加载：初载只取顶层（max_depth=1），点节点展开时以该 id 为 root 再查子层
 * （后端 ${apiUrl}/concepts/{type}/tree?root= 递归 BFS 聚合 /conceptInstance/level）。
 * 前端按需拉取，避免大层级图一次性打爆响应。容错提示 + 深浅层次可折叠。
 */

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight, ListTree } from "lucide-react";

import { fetchKagConceptTree } from "../api";
import type { KagConceptTreeNode, SpgTypeRow } from "../model";

interface ConceptTreePanelProps {
  projectId: string;
  /** 概念类型行（typeRow.key = 限定全名，如 m0ProbeLive.Topic）。 */
  typeRow: SpgTypeRow;
}

/** 初载只取顶层（1 层），子层按需懒加载。 */
const INITIAL_DEPTH = 1;

export function ConceptTreePanel({ projectId, typeRow }: ConceptTreePanelProps) {
  const { t } = useTranslation();
  const [top, setTop] = useState<KagConceptTreeNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [truncated, setTruncated] = useState(false);
  /** 已展开的节点 id 集合。 */
  const [open, setOpen] = useState<Set<string>>(new Set());
  /** 各节点懒加载到的子层缓存（nodeId -> children）。 */
  const [loaded, setLoaded] = useState<Record<string, KagConceptTreeNode[]>>({});
  const cancelledRef = useRef(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const tree = await fetchKagConceptTree(projectId, typeRow.key, {
        maxDepth: INITIAL_DEPTH,
      });
      if (cancelledRef.current) return;
      setTop(tree.children);
      setTruncated(tree.truncated);
    } catch (cause) {
      if (cancelledRef.current) return;
      setError(cause instanceof Error ? cause.message : t("The concept tree could not be loaded"));
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

  async function toggle(node: KagConceptTreeNode) {
    const id = node.id;
    if (!id) return;
    if (open.has(id)) {
      setOpen((prev) => new Set([...prev].filter((x) => x !== id)));
      return;
    }
    if (!loaded[id]) {
      // 懒加载该节点的子层（以自身为 root 展开）
      try {
        const next = await fetchKagConceptTree(projectId, typeRow.key, { root: id });
        if (cancelledRef.current) return;
        setLoaded((prev) => ({ ...prev, [id]: next.children }));
        setTruncated((prev) => prev || next.truncated);
        setOpen((prev) => new Set(prev).add(id));
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : t("The concept tree could not be loaded"));
      }
    } else {
      setOpen((prev) => new Set(prev).add(id));
    }
  }

  return (
    <div className="mt-2 space-y-2 rounded-lg border border-[var(--border)]/50 bg-[var(--background)]/40 p-3">
      <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[var(--muted-foreground)]">
        <ListTree size={12} aria-hidden />
        {t("Concept tree")}
      </div>

      {loading ? (
        <p className="px-1 text-[11.5px] text-[var(--muted-foreground)]">
          {t("Loading")}
        </p>
      ) : error ? (
        <p className="px-1 text-[11.5px] text-red-600">{error}</p>
      ) : top.length === 0 ? (
        <p className="px-1 text-[11.5px] text-[var(--muted-foreground)]">
          {t("No concepts in this tree")}
        </p>
      ) : (
        <ul className="space-y-0.5">
          {top.map((node) => (
            <TreeNodeRow
              key={node.id}
              node={node}
              depth={0}
              open={open}
              loaded={loaded}
              onToggle={toggle}
            />
          ))}
        </ul>
      )}
      {truncated && (
        <p className="px-1 text-[11px] text-amber-600">
          {t("The concept tree was truncated because it is large")}
        </p>
      )}
    </div>
  );
}

interface TreeNodeRowProps {
  node: KagConceptTreeNode;
  depth: number;
  open: Set<string>;
  loaded: Record<string, KagConceptTreeNode[]>;
  onToggle: (node: KagConceptTreeNode) => void;
}

function TreeNodeRow({ node, depth, open, loaded, onToggle }: TreeNodeRowProps) {
  const { t } = useTranslation();
  const id = node.id;
  const lazyChildren = loaded[id];
  // 已懒加载的子层优先；否则用初始（顶层=空，未截断节点子层尚未取）
  const children = lazyChildren ?? node.children;
  const isOpen = open.has(id);

  if (node.nodeCapReached) {
    return (
      <li className="flex items-center gap-1 px-1 text-[11.5px] text-[var(--muted-foreground)]">
        {t("(more concepts… could not be shown)")}
      </li>
    );
  }

  return (
    <li>
      <button
        type="button"
        onClick={() => onToggle(node)}
        className="flex items-center gap-0.5 rounded px-1 py-0.5 text-left text-[11.5px] text-[var(--foreground)] hover:bg-[var(--card)]/60"
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        aria-expanded={isOpen}
      >
        <ChevronRight
          size={12}
          aria-hidden
          className={`text-[var(--muted-foreground)] transition-transform ${isOpen ? "rotate-90" : ""}`}
        />
        <span className="font-mono">{node.name || node.id || "…"}</span>
      </button>
      {isOpen ? (
        children && children.length > 0 ? (
          <ul className="space-y-0.5">
            {children.map((child) => (
              <TreeNodeRow
                key={child.id}
                node={child}
                depth={depth + 1}
                open={open}
                loaded={loaded}
                onToggle={onToggle}
              />
            ))}
          </ul>
        ) : (
          <p
            className="px-1 text-[11px] text-[var(--muted-foreground)]"
            style={{ paddingLeft: `${8 + (depth + 1) * 14}px` }}
          >
            {t("No sub-concepts")}
          </p>
        )
      ) : null}
    </li>
  );
}