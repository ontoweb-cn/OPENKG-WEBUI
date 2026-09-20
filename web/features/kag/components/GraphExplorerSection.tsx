"use client";

/**
 * M3.4 图浏览：reason DSL 查询 + 表格 + cytoscape 画布。
 *
 * 数据源经 /api/kag/projects/{id}/graph/query（/public/v1/reason/run——graph
 * 控制器无子图查询端点，M2 侦察修正）。2 列 id 的 rows 直接映射为边
 * （rowsToGraph）；模板按钮按 Schema 类型生成 DSL（节点类型须 namespace
 * 全名，关系 label 裸名——M3.3 实测契约）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";
import { Network, Play } from "lucide-react";

import { queryKagGraph } from "../api";
import {
  buildNodeTypes,
  oneHopDsl,
  parseDslRelationLabel,
  rowsToGraph,
  twoHopDsl,
  type NodeTypeInfo,
  type SpgTypeRow,
} from "../model";

cytoscape.use(dagre);

export function GraphExplorerSection({
  projectId,
  types,
}: {
  projectId: string;
  types: SpgTypeRow[];
}) {
  const { t } = useTranslation();
  const [dsl, setDsl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{
    header: string[];
    rows: unknown[];
    rowCount: number;
    truncated: boolean;
  } | null>(null);
  const [renderKey, setRenderKey] = useState(0);
  // C1：节点点击详情/一跳——记录执行过的 DSL 供类型解析（输入框编辑不影响）
  const [executedDsl, setExecutedDsl] = useState("");
  const [selected, setSelected] = useState<{ id: string; type: string | null } | null>(
    null,
  );

  const business = useMemo(
    () => types.filter((row) => row.kind !== "basic" && row.kind !== "standard"),
    [types],
  );
  const graph = useMemo(
    () => (result ? rowsToGraph(result.rows) : { nodes: [], edges: [] }),
    [result],
  );
  const nodeTypes = useMemo(
    () =>
      result
        ? buildNodeTypes(executedDsl, result.header, result.rows)
        : new Map<string, NodeTypeInfo>(),
    [result, executedDsl],
  );

  async function run(dslValue?: string) {
    const query = (dslValue ?? dsl).trim();
    if (!query) {
      setError(t("Enter a DSL query first"));
      return;
    }
    setError("");
    setBusy(true);
    try {
      const res = await queryKagGraph(projectId, { dsl: query });
      setExecutedDsl(query);
      setSelected(null);
      if (res.error) {
        setError(res.error.split("\n")[0]);
        setResult(null);
        return;
      }
      setResult({
        header: res.header,
        rows: res.rows,
        rowCount: res.rowCount,
        truncated: res.truncated,
      });
      setRenderKey((k) => k + 1);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("The graph query was rejected"),
      );
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  const handleNodeTap = useCallback(
    (id: string) => {
      const info = nodeTypes.get(id);
      setSelected({ id, type: info?.type ?? null });
    },
    [nodeTypes],
  );

  function expandOneHop(id: string, type: string) {
    if (busy) return; // 评审 P3-1：查询进行中不再触发并发覆盖
    const query = oneHopDsl(type, parseDslRelationLabel(executedDsl), id);
    setDsl(query);
    void run(query);
  }

  const firstType = business[0]?.key ?? "Namespace.Type";

  return (
    <section className="mb-8">
      <h2 className="mb-2 text-[15px] font-semibold tracking-tight text-[var(--foreground)]">
        {t("Graph explorer")}
      </h2>
      <p className="mb-3 text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
        {t(
          "Run a read-only reason DSL query. Only the start node type is constrained (namespace-qualified full name); relation labels and target node type constraints are not supported. Two-column id results render as a graph. The DSL takes no LIMIT clause - results are capped server-side.",
        )}
      </p>
      <div className="mb-2 flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={() => setDsl(`MATCH (n:${firstType}) RETURN n.id, n.name`)}
          className="rounded-full border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1 font-mono text-[11px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/50"
        >
          {t("Node template")}
        </button>
        <button
          type="button"
          onClick={() =>
            setDsl(`MATCH (n:${firstType})-[p]->(o) RETURN n.id, o.id`)
          }
          className="rounded-full border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1 font-mono text-[11px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/50"
        >
          {t("Relation template")}
        </button>
        <button
          type="button"
          onClick={() => setDsl(twoHopDsl(firstType, firstType, firstType))}
          className="rounded-full border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1 font-mono text-[11px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/50"
        >
          {t("2-hop template")}
        </button>
      </div>
      <div className="flex gap-2">
        <input
          value={dsl}
          disabled={busy}
          onChange={(e) => setDsl(e.target.value)}
          placeholder="MATCH (n:ns.Type)-[p:rel]->(o:ns.Other) RETURN n.id, o.id"
          className="min-w-0 flex-1 rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-3 py-2 font-mono text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => void run()}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-[var(--primary)] px-3.5 py-2 text-[12px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          <Play size={12} aria-hidden />
          {busy ? t("Running…") : t("Run query")}
        </button>
      </div>
      {error ? (
        <p className="mt-2 rounded-md bg-red-500/10 px-3 py-2 font-mono text-[11.5px] text-red-600 dark:text-red-400">
          {error}
        </p>
      ) : null}

      {result ? (
        <div className="mt-3 space-y-3">
          {result.truncated ? (
            <p className="text-[11.5px] text-[var(--muted-foreground)]">
              {t("Results were truncated to the first 200 rows")}
            </p>
          ) : null}
          {graph.edges.length > 0 ? (
            <div className="flex items-center gap-1.5 text-[12px] text-[var(--muted-foreground)]">
              <Network size={13} aria-hidden />
              {t("{{nodes}} nodes, {{edges}} edges", {
                nodes: graph.nodes.length,
                edges: graph.edges.length,
              })}
            </div>
          ) : null}
          <GraphCanvas
            key={renderKey}
            nodes={graph.nodes}
            edges={graph.edges}
            onNodeTap={handleNodeTap}
          />
          {selected ? (
            <NodeDetailPanel
              id={selected.id}
              type={selected.type}
              header={result.header}
              matching={result.rows.filter(
                (row) =>
                  Array.isArray(row) &&
                  (String(row[0] ?? "") === selected.id ||
                    String(row[1] ?? "") === selected.id),
              )}
              onExpandOneHop={() =>
                selected.type ? expandOneHop(selected.id, selected.type) : undefined
              }
              expandable={selected.type !== null}
            />
          ) : null}
          <div className="overflow-x-auto rounded-xl border border-[var(--border)]/60 bg-[var(--card)]">
            <table className="w-full text-left text-[12px]">
              <thead>
                <tr className="border-b border-[var(--border)]/60 text-[11px] text-[var(--muted-foreground)]">
                  {result.header.map((h) => (
                    <th key={h} className="px-3 py-2 font-mono font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.slice(0, 50).map((row, idx) => (
                  <tr
                    key={idx}
                    className="border-b border-[var(--border)]/30 last:border-0"
                  >
                    {Array.isArray(row)
                      ? row.map((cell, i) => (
                          <td
                            key={i}
                            className="px-3 py-1.5 font-mono text-[var(--foreground)]"
                          >
                            {String(cell ?? "")}
                          </td>
                        ))
                      : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-[var(--muted-foreground)]">
            {t("{{count}} row(s) returned", { count: result.rowCount })}
          </p>
        </div>
      ) : null}
    </section>
  );
}

function NodeDetailPanel({
  id,
  type,
  header,
  matching,
  onExpandOneHop,
  expandable,
}: {
  id: string;
  type: string | null;
  header: string[];
  matching: unknown[];
  onExpandOneHop: () => void;
  expandable: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-mono text-[12px] font-medium text-[var(--foreground)]">{id}</p>
          <p className="mt-0.5 text-[10.5px] text-[var(--muted-foreground)]">
            {t("Node type")}: {type ?? t("Unavailable")}
          </p>
        </div>
        <button
          type="button"
          disabled={!expandable}
          onClick={onExpandOneHop}
          className="shrink-0 rounded-md border border-[var(--border)]/60 px-2.5 py-1.5 text-[11.5px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/40 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {t("Expand 1-hop")}
        </button>
      </div>
      {type === null ? (
        <p className="mt-2 text-[11px] text-[var(--muted-foreground)]">
          {t("No type could be parsed from the DSL; entity detail is unavailable.")}
        </p>
      ) : (
        <p className="mt-2 text-[10.5px] text-[var(--muted-foreground)]">
          {t(
            "One-hop queries follow the M3.3 DSL contract and were verified against the live graph store.",
          )}
        </p>
      )}
      {matching.length > 0 ? (
        <div className="mt-2 overflow-x-auto rounded-lg border border-[var(--border)]/40">
          <table className="w-full text-left text-[11.5px]">
            <thead>
              <tr className="border-b border-[var(--border)]/40 text-[10.5px] text-[var(--muted-foreground)]">
                {header.map((h) => (
                  <th key={h} className="px-2.5 py-1.5 font-mono font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matching.slice(0, 20).map((row, idx) => (
                <tr key={idx} className="border-b border-[var(--border)]/20 last:border-0">
                  {Array.isArray(row)
                    ? row.map((cell, i) => (
                        <td
                          key={i}
                          className="px-2.5 py-1.5 font-mono text-[var(--foreground)]"
                        >
                          {String(cell ?? "")}
                        </td>
                      ))
                    : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-2 text-[11px] text-[var(--muted-foreground)]">
          {t("No matching row in the current result")}
        </p>
      )}
    </div>
  );
}

function GraphCanvas({
  nodes,
  edges,
  onNodeTap,
}: {
  nodes: string[];
  edges: { source: string; target: string }[];
  onNodeTap?: (id: string) => void;
}) {
  const { t } = useTranslation();
  if (nodes.length === 0) return null;
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--border)]/60 bg-[var(--card)]">
      <CytoscapeMount nodes={nodes} edges={edges} onNodeTap={onNodeTap} />
      <p className="border-t border-[var(--border)]/40 px-3 py-1.5 text-[10.5px] text-[var(--muted-foreground)]">
        {t("Drag to pan, scroll to zoom. Click a node to inspect it.")}
      </p>
    </div>
  );
}

function CytoscapeMount({
  nodes,
  edges,
  onNodeTap,
}: {
  nodes: string[];
  edges: { source: string; target: string }[];
  onNodeTap?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement | null>(null);
  // 30 个节点以上用摘要代替（画布过密不可读）
  const capped = nodes.slice(0, 30);
  const nodeKey = nodes.join(",");
  const edgeKey = edges.map((e) => `${e.source}>${e.target}`).join(",");
  useEffect(() => {
    if (!ref.current) return;
    const elements = [
      ...capped.map((id) => ({ data: { id, label: id } })),
      ...edges
        .filter((e) => capped.includes(e.source) && capped.includes(e.target))
        .map((e) => ({ data: { source: e.source, target: e.target } })),
    ];
    const cy = cytoscape({
      container: ref.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-size": 9,
            color: "var(--foreground)",
            "background-color": "var(--primary)",
            width: 14,
            height: 14,
            "text-valign": "bottom",
            "text-margin-y": 4,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "var(--muted-foreground)",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.6,
          },
        },
      ],
      // dagre 扩展的布局选项（rankDir 等）不在 cytoscape 核心类型里
      layout: {
        name: "dagre",
        rankDir: "LR",
        nodeSep: 24,
        rankSep: 48,
      } as unknown as cytoscape.LayoutOptions,
    });
    if (onNodeTap) cy.on("tap", "node", (evt) => onNodeTap(evt.target.id()));
    return () => cy.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeKey, edgeKey, onNodeTap]);
  if (nodes.length > 30) {
    return (
      <div className="px-4 py-6 text-center text-[12px] text-[var(--muted-foreground)]">
        {t("Too many nodes to render ({{count}}); refine the query", {
          count: nodes.length,
        })}
      </div>
    );
  }
  return <div ref={ref} className="h-64 w-full" aria-label={t("Graph canvas")} />;
}
