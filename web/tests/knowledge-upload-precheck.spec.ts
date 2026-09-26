/**
 * 知识域上传预校验（P0-T3）纯函数回归。
 * 预览渲染器分流复用 components/chat/preview/previewerFor（自带测试面），
 * 此处只覆盖上传预校验。
 */

import { describe, expect, it } from "vitest";

import {
  extensionOf,
  isSupportedExtension,
  precheckUploadFiles,
} from "@/features/knowledge/upload-precheck";

function file(name: string, size = 10): File {
  // size 以字节体现到内容，保证 name+size 去重键可区分
  return new File(["x".repeat(size)], name, { type: "application/octet-stream", lastModified: 0 });
}

describe("precheckUploadFiles", () => {
  it("dedupes by name+size keeping first occurrence", () => {
    const items = precheckUploadFiles([file("a.pdf", 1), file("a.pdf", 1), file("a.pdf", 2)]);
    expect(items).toHaveLength(2);
    expect(items[0].file.size).toBe(1);
    expect(items[1].file.size).toBe(2);
  });

  it("marks allowlisted extensions as supported", () => {
    const items = precheckUploadFiles([file("doc.pdf"), file("notes.md"), file("data.csv")]);
    expect(items.every((item) => item.status === "supported")).toBe(true);
  });

  it("marks unknown extensions as unknown (still uploadable)", () => {
    const items = precheckUploadFiles([file("tool.exe"), file("archive.7z"), file("noext")]);
    expect(items.every((item) => item.status === "unknown")).toBe(true);
  });

  it("preserves input order after merge", () => {
    const items = precheckUploadFiles([file("b.txt"), file("a.pdf"), file("b.txt")]);
    expect(items.map((item) => item.file.name)).toEqual(["b.txt", "a.pdf"]);
  });
});

describe("extensionOf", () => {
  it("handles dots, case and missing extensions", () => {
    expect(extensionOf("A.PDF")).toBe("pdf");
    expect(extensionOf("archive.tar.gz")).toBe("gz");
    expect(extensionOf("noext")).toBe("");
    expect(extensionOf(".hidden")).toBe("");
  });

  it("matches the upload allowlist", () => {
    expect(isSupportedExtension("report.docx")).toBe(true);
    expect(isSupportedExtension("payload.EXE")).toBe(false);
  });
});

// —— P3 T14'：progressHint 最小映射 ——

import { progressHint } from "@/features/knowledge/model";

const t = (key: string) => key;

describe("progressHint", () => {
  it("maps known failure patterns to localized hints", () => {
    expect(progressHint("18:34:16 [ERROR][Exception]: Provider  not found for model .", t)).toBe(
      "The RAG server has no chat model configured for this operation.",
    );
    expect(
      progressHint("AssertionError: The dimension (1024) of given embedding model is different from the original (768)", t),
    ).toBe("Embedding dimension mismatch — the vector index must be rebuilt.");
    expect(progressHint("bla [ERROR] boom", t)).toBe("An error occurred during parsing.");
  });

  it("returns null for unmatched free text", () => {
    expect(progressHint("parsing docs 3/10", t)).toBeNull();
    expect(progressHint("", t)).toBeNull();
  });

  it("prioritizes the most specific pattern", () => {
    // Provider not found 同时含 [ERROR] —— 具体模式优先
    const hint = progressHint("[ERROR]: Provider  not found for model .", t);
    expect(hint).toBe("The RAG server has no chat model configured for this operation.");
  });
});

// —— P3 T12：能力 → 详情页 tab 映射（R7 映射表落码）——

import { detailTabsForCapabilities } from "@/features/knowledge/model";

describe("detailTabsForCapabilities", () => {
  it("returns the full tab set for null (catalog not yet loaded)", () => {
    expect(detailTabsForCapabilities(null)).toEqual([
      "documents", "sources", "retrieval", "graph", "settings",
    ]);
  });

  it("keeps documents/settings always and gates by capability", () => {
    const all = ["upload", "structured_upload", "search", "delete", "sources", "logs", "preview", "chat_binding", "mcp_binding", "graph_index"];
    expect(detailTabsForCapabilities(all)).toEqual([
      "documents", "sources", "retrieval", "graph", "settings",
    ]);
    const minimal = ["search"];
    expect(detailTabsForCapabilities(minimal)).toEqual(["documents", "retrieval", "settings"]);
    expect(detailTabsForCapabilities([])).toEqual(["documents", "settings"]);
  });
});
