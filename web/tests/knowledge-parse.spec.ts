/**
 * 知识域 parse 层（Phase 3 T3）：域形状解析回归。
 *
 * 覆盖 T3 的形状变更——后端出站为域模型（`{datasets, total}` /
 * `{documents, total}` / `{chunks, total, denied_dataset_ids}` / `[IngestionLog]`），
 * 不再有 `{code, data, message}` 信封。这些断言是后续引擎接入的形状基准。
 */

import { describe, expect, it } from "vitest";

import {
  parseIngestionLogs,
  parseKnowledgeDatasets,
  parseKnowledgeDocuments,
  parseSearchChunks,
} from "@/features/knowledge/model";

describe("parseKnowledgeDatasets", () => {
  it("parses the T3 domain shape {datasets, total}", () => {
    const result = parseKnowledgeDatasets({
      datasets: [
        {
          id: "kb-1",
          name: "KB",
          description: "d",
          permission: "team",
          document_count: 3,
          chunk_count: 9,
          token_count: 100,
          created_at: "1700000000000",
        },
      ],
      total: 1,
    });
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      id: "kb-1",
      name: "KB",
      permission: "team",
      documentCount: 3,
      chunkCount: 9,
    });
  });

  it("tolerates a bare array (legacy fallback)", () => {
    const result = parseKnowledgeDatasets([{ id: "kb-2", name: "Bare" }]);
    expect(result.map((d) => d.id)).toEqual(["kb-2"]);
  });

  it("drops rows without id or name", () => {
    expect(parseKnowledgeDatasets({ datasets: [{ id: "x" }, { name: "y" }] })).toEqual([]);
  });
});

describe("parseKnowledgeDocuments", () => {
  it("parses {documents, total} with run normalization and clamped progress", () => {
    const result = parseKnowledgeDocuments({
      documents: [
        { id: "doc-1", name: "a.md", run: "done", progress: 150, chunk_count: 2, size: 10 },
        { id: "doc-2", name: "b.md", run: "weird", progress: -5 },
      ],
      total: 2,
    });
    expect(result.total).toBe(2);
    expect(result.documents[0]).toMatchObject({ run: "DONE", progress: 100 });
    // 未知状态回落 UNSTART；进度下钳到 0
    expect(result.documents[1]).toMatchObject({ run: "UNSTART", progress: 0 });
  });
});

describe("parseSearchChunks", () => {
  it("parses {chunks, total, denied_dataset_ids}", () => {
    const chunks = parseSearchChunks({
      chunks: [
        {
          id: "c1",
          content: "text",
          similarity: 0.42,
          document_name: "a.md",
        },
      ],
      total: 1,
      denied_dataset_ids: ["kb-x"],
    });
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toMatchObject({ id: "c1", documentName: "a.md", similarity: 0.42 });
  });

  it("returns [] for an empty result", () => {
    expect(parseSearchChunks({ chunks: [], total: 0, denied_dataset_ids: [] })).toEqual([]);
  });
});

describe("parseIngestionLogs", () => {
  it("parses the bare-array domain shape (T3)", () => {
    const logs = parseIngestionLogs([
      { id: "l1", progress: 0.8, message: "parsing", status: "1", document_name: "a.md" },
    ]);
    expect(logs).toHaveLength(1);
    expect(logs[0]).toMatchObject({
      id: "l1",
      message: "parsing",
      documentName: "a.md",
      status: "1",
    });
  });

  it("tolerates the SSE wrapper {logs: [...]}", () => {
    const logs = parseIngestionLogs({ logs: [{ id: "l2", message: "x" }] });
    expect(logs.map((l) => l.id)).toEqual(["l2"]);
  });
});
