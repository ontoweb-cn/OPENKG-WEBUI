"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import cytoscape, { type Core, type ElementsDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import { AlertTriangle, Network, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";

import { buildIndex, deleteIndex, fetchIndexStatus, fetchKnowledgeGraph } from "../api";
import type { KnowledgeGraphData } from "../model";

/**
 * 知识图谱面板（P2-T10）：未构建 → 空态 + 显式构建按钮（graph/raptor，R3 构建中
 * 禁用 + 轮询 5s）；就绪 → cytoscape/fcose 画布（R2：节点 >500 按 degree 采样）；
 * 危险区删除。构建成本高，不做自动触发。
 */

cytoscape.use(fcose);

const NODE_LIMIT = 500;
const POLL_MS = 5000;

function sampleGraph(data: KnowledgeGraphData): KnowledgeGraphData {
  if (data.nodes.length <= NODE_LIMIT) return data;
  const degree = new Map<string, number>();
  for (const edge of data.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  const kept = [...data.nodes]
    .sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0))
    .slice(0, NODE_LIMIT);
  const ids = new Set(kept.map((node) => node.id));
  const keptEdges = data.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  return { nodes: kept, edges: keptEdges };
}

export default function KnowledgeGraphPanel({ datasetId }: { datasetId: string }) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [graph, setGraph] = useState<KnowledgeGraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [building, setBuilding] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [graphData, indexStatus] = await Promise.all([
        fetchKnowledgeGraph(datasetId),
        fetchIndexStatus(datasetId, "graph").catch(() => ({})),
      ]);
      setGraph(graphData);
      setStatus(indexStatus);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [datasetId]);

  useEffect(() => {
    void load();
  }, [load, reloadTick]);

  const hasGraph = (graph?.nodes.length ?? 0) > 0;
  // R4：status 非空即视为有任务信息——图未就绪且状态非空 → 轮询
  const hasStatus = status != null && Object.keys(status).length > 0;
  const polling = !hasGraph && hasStatus;

  useEffect(() => {
    if (!polling) return;
    const timer = setInterval(() => setReloadTick((value) => value + 1), POLL_MS);
    return () => clearInterval(timer);
  }, [polling]);

  // 画布：数据就绪时渲染（数据变化重建）
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !graph || graph.nodes.length === 0) return;
    const sampled = sampleGraph(graph);
    const elements: ElementsDefinition = {
      nodes: sampled.nodes.map((node) => ({
        data: { id: node.id, label: node.label },
      })),
      edges: sampled.edges.map((edge, index) => ({
        data: {
          id: `e${index}`,
          source: edge.source,
          target: edge.target,
          label: edge.label,
        },
      })),
    };
    const cy = cytoscape({
      container,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-size": 8,
            color: "var(--muted-foreground)",
            "background-color": "var(--primary)",
            width: 12,
            height: 12,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1,
            "line-color": "var(--border)",
            "curve-style": "bezier",
            opacity: 0.6,
          },
        },
      ],
      layout: { name: "fcose", animate: false, randomize: true } as never,
      wheelSensitivity: 0.2,
    });
    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [graph]);

  const onBuild = async (indexType: "graph" | "raptor") => {
    setBuilding(true);
    setError(null);
    try {
      await buildIndex(datasetId, indexType);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBuilding(false);
    }
  };

  const onDelete = async () => {
    setBuilding(true);
    try {
      await deleteIndex(datasetId, "graph");
      setConfirmDelete(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBuilding(false);
    }
  };

  return (
    <div className="max-w-5xl">
      {error ? (
        <p className="mb-3 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-2.5 text-[12.5px] text-[var(--foreground)]">
          {error}
        </p>
      ) : null}

      {loading ? (
        <div className="flex h-64 items-center justify-center text-sm text-[var(--muted-foreground)]">
          {t("Loading...")}
        </div>
      ) : hasGraph ? (
        <div className="overflow-hidden rounded-xl border border-[var(--border)]/60 bg-[var(--card)]">
          <div className="flex items-center justify-between gap-2 border-b border-[var(--border)]/60 px-4 py-2.5 text-[12.5px] text-[var(--muted-foreground)]">
            <span className="flex items-center gap-1.5">
              <Network size={13} />
              {t("{{count}} nodes · {{count2}} edges", {
                count: graph?.nodes.length ?? 0,
                count2: graph?.edges.length ?? 0,
              })}
              {(graph?.nodes.length ?? 0) > NODE_LIMIT
                ? ` · ${t("sampled to top {{count}} by degree", { count: NODE_LIMIT })}`
                : ""}
            </span>
            {confirmDelete ? (
              <span className="flex items-center gap-1.5">
                <AlertTriangle size={12} className="text-destructive" />
                <Button variant="ghost" size="sm" disabled={building} onClick={onDelete}>
                  {t("Confirm delete?")}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(false)}>
                  {t("Cancel")}
                </Button>
              </span>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                icon={<Trash2 size={12} />}
                disabled={building}
                onClick={() => setConfirmDelete(true)}
              >
                {t("Delete graph")}
              </Button>
            )}
          </div>
          <div ref={containerRef} className="h-[480px] w-full" />
        </div>
      ) : polling ? (
        <div className="rounded-xl border border-dashed border-[var(--border)]/70 bg-[var(--card)]/50 px-6 py-10 text-center">
          <p className="text-[13px] text-[var(--foreground)]">{t("Graph index is building…")}</p>
          <pre className="mx-auto mt-3 max-w-md overflow-x-auto rounded bg-[var(--muted)]/30 p-2 text-left font-mono text-[11px] text-[var(--muted-foreground)]">
            {JSON.stringify(status, null, 2).slice(0, 400)}
          </pre>
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-[var(--border)]/70 bg-[var(--card)]/50 px-6 py-10 text-center">
          <p className="text-[13px] text-[var(--foreground)]">
            {t("No knowledge graph yet. Build one from the parsed documents.")}
          </p>
          <p className="mt-1 text-[11.5px] text-[var(--muted-foreground)]">
            {t("Graph builds run on the server and may take a while; raptor builds a summary tree instead.")}
          </p>
          <div className="mt-4 flex items-center justify-center gap-2">
            <Button
              variant="primary"
              size="sm"
              loading={building}
              onClick={() => onBuild("graph")}
            >
              {t("Build graph index")}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              loading={building}
              onClick={() => onBuild("raptor")}
            >
              {t("Build raptor index")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
