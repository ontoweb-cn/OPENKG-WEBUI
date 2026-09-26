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
