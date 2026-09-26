/**
 * 上传预校验（P0-T3）：拖放/选择后的纯函数检查——不渲染、不发请求。
 *
 * 口径（P0 任务清单 §二-T3）：
 * - 去重：name+size 相同视为重复，直接剔除（不进 accepted）；
 * - 未知扩展名**允许上传**（上游接受远不止 allowlist 的格式），
 *   仅以 "unknown" 状态提示用户确认——客户端不做硬拦截；
 * - zip 与普通文件在上传层（KnowledgeDetailPage.onUpload）分流，
 *   预校验不区分。
 */

export interface PrecheckItem {
  file: File;
  /** supported = allowlist 命中；unknown = 未识别类型（仍可上传） */
  status: "supported" | "unknown";
}

/** 常见可解析文档格式（展示用 allowlist；非安全边界）。 */
const SUPPORTED_EXTENSIONS = new Set([
  // 文档
  "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "csv", "txt", "md", "markdown",
  "epub", "html", "htm", "xml", "json", "yaml", "yml", "rtf", "odt",
  // 图片
  "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tiff", "tif",
  // 归档（zip 走结构化解包端点）
  "zip",
  // 常见代码/文本
  "py", "ts", "tsx", "js", "jsx", "css", "log", "sql", "sh", "toml", "ini",
]);

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  if (dot <= 0 || dot === name.length - 1) return "";
  return name.slice(dot + 1).toLowerCase();
}

export function isSupportedExtension(name: string): boolean {
  return SUPPORTED_EXTENSIONS.has(extensionOf(name));
}

/**
 * 预校验一组文件：剔除重复（name+size），其余按扩展名标注 supported/unknown。
 * 顺序保持原序；同批内先出现者保留。
 */
export function precheckUploadFiles(files: File[]): PrecheckItem[] {
  const seen = new Set<string>();
  const items: PrecheckItem[] = [];
  for (const file of files) {
    const key = `${file.name}\u0000${file.size}`;
    if (seen.has(key)) continue;
    seen.add(key);
    items.push({
      file,
      status: isSupportedExtension(file.name) ? "supported" : "unknown",
    });
  }
  return items;
}

