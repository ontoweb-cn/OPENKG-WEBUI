/**
 * KAG 管理面纯模型层（设计 docs/kag-integration-design.md §5.4）。
 *
 * 只做契约归一与校验，不渲染、不依赖 app/components/context——
 * `feature-domain-does-not-render` 分层规则由 depcruise 守护。
 * 上游字段缺失/类型漂移时降级为安全缺省值，列表页始终可渲染。
 */

// —— 项目（OpenSPG /public/v1/project 原始行的宽松归一）——

export interface KagProject {
  projectId: string;
  name: string;
  namespace: string;
  description: string;
  /** LOCAL / PUBLIC_NET / …；上游缺省为空串。 */
  tag: string;
  visibility: string;
  userNo: string;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function text(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function textOrNull(value: unknown): string | null {
  const s = text(value);
  return s === "" || s === "None" ? null : s;
}

function parseProject(raw: unknown): KagProject {
  const row = record(raw);
  return {
    projectId: text(row.id ?? row.projectId),
    name: text(row.name),
    namespace: text(row.namespace),
    description: textOrNull(row.description) ?? "",
    tag: text(row.tag),
    visibility: text(row.visibility),
    userNo: text(row.userNo),
  };
}

export function parseKagProjects(raw: unknown): KagProject[] {
  const payload = record(raw);
  const rows = payload.projects;
  if (!Array.isArray(rows)) return [];
  return rows.map(parseProject);
}

export function parseKagProject(raw: unknown): KagProject | null {
  const payload = record(raw);
  const project = payload.project ?? raw;
  if (!record(project).id && !record(project).projectId) return null;
  return parseProject(project);
}

// —— 项目详情（GET /api/kag/projects/{id}）——

export interface KagProjectDetail {
  project: KagProject;
  schemaSummary: { spgTypeCount: number; spgTypeNames: string[] } | null;
  graphLabels: string[] | null;
}

export function parseKagProjectDetail(raw: unknown): KagProjectDetail | null {
  const payload = record(raw);
  const project = parseKagProject(raw);
  if (!project) return null;
  const summary = record(payload.schema_summary);
  const names = Array.isArray(summary.spg_type_names)
    ? summary.spg_type_names.map(text).filter(Boolean)
    : null;
  // 后端 spg_type_count 是真实数；spg_type_names 截断到 100（kag.py），
  // >100 类型的项目以 names.length 计数会偏小——count 字段优先，names 仅 fallback。
  const reported = Number(summary.spg_type_count);
  const typeCount =
    Number.isInteger(reported) && reported >= 0
      ? reported
      : (names?.length ?? 0);
  const labels = Array.isArray(payload.graph_labels)
    ? payload.graph_labels.map(text).filter(Boolean)
    : null;
  return {
    project,
    schemaSummary:
      names === null ? null : { spgTypeCount: typeCount, spgTypeNames: names },
    graphLabels: labels,
  };
}

// —— Schema（spgTypes 原始行 → 只读树行）——

export type SpgTypeKind =
  | "basic"
  | "index"
  | "standard"
  | "entity"
  | "concept"
  | "event"
  | "unknown";

const SPG_TYPE_KINDS: Record<string, SpgTypeKind> = {
  BASIC_TYPE: "basic",
  INDEX_TYPE: "index",
  STANDARD_TYPE: "standard",
  ENTITY_TYPE: "entity",
  CONCEPT_TYPE: "concept",
  EVENT_TYPE: "event",
};

export interface SpgPropertyRow {
  name: string;
  nameZh: string;
  objectType: string;
  inherited: boolean;
}

export interface SpgTypeRow {
  /** 限定名（namespace + nameEn）；basic 类型无 namespace。 */
  key: string;
  name: string;
  namespace: string;
  nameZh: string;
  desc: string;
  kind: SpgTypeKind;
  parent: string | null;
  properties: SpgPropertyRow[];
  /** 原始读模型 JSON（M3.5 编辑端点要求原样回传——wire 转换在服务端）。 */
  raw: Record<string, unknown>;
}

function qualifiedName(basicInfo: Record<string, unknown>): string {
  const name = record(basicInfo.name);
  const ns = textOrNull(name.namespace);
  const nameEn = text(name.nameEn ?? name.name);
  return ns && nameEn ? `${ns}.${nameEn}` : nameEn;
}

function parseProperty(raw: unknown): SpgPropertyRow {
  const row = record(raw);
  const info = record(row.basicInfo);
  const predicate = record(info.name);
  const objectType = qualifiedName(record(record(row.objectTypeRef).basicInfo));
  return {
    name: text(predicate.name),
    nameZh: text(info.nameZh),
    objectType,
    inherited: row.inherited === true,
  };
}

/** parentTypeIdentifier 的字段直接挂在自身（无 basicInfo.name 包层）。 */
function parentQualifiedName(raw: unknown): string {
  const identifier = record(raw);
  const ns = textOrNull(identifier.namespace);
  const nameEn = text(identifier.nameEn);
  return ns && nameEn ? `${ns}.${nameEn}` : nameEn;
}

function parseSpgType(raw: unknown): SpgTypeRow {
  const row = record(raw);
  const info = record(row.basicInfo);
  const name = record(info.name);
  const kind = SPG_TYPE_KINDS[text(row.spgTypeEnum)] ?? "unknown";
  const key = qualifiedName(info) || text(name.nameEn);
  const parent = parentQualifiedName(
    record(record(row.parentTypeInfo).parentTypeIdentifier),
  );
  const properties = Array.isArray(row.properties)
    ? row.properties.map(parseProperty)
    : [];
  return {
    key: key || text(name.name),
    name: text(name.nameEn ?? name.name),
    namespace: text(name.namespace),
    nameZh: text(info.nameZh),
    desc: textOrNull(info.desc) ?? "",
    kind,
    parent: parent && parent !== key ? parent : null,
    properties,
    raw: row,
  };
}

export function parseSpgSchema(raw: unknown): SpgTypeRow[] {
  const payload = record(raw);
  const types = record(payload.schema).spgTypes ?? payload.spgTypes;
  if (!Array.isArray(types)) return [];
  return types.map(parseSpgType).filter((row) => row.key !== "");
}

/** 只读树节点（实体/概念/事件类型按 parentTypeInfo 层级化，basic 平铺）。 */
export interface SpgTypeNode extends SpgTypeRow {
  children: SpgTypeNode[];
}

export function buildSpgTypeTree(rows: SpgTypeRow[]): SpgTypeNode[] {
  const byKey = new Map<string, SpgTypeNode>();
  for (const row of rows) {
    if (!byKey.has(row.key)) byKey.set(row.key, { ...row, children: [] });
  }
  const roots: SpgTypeNode[] = [];
  for (const node of byKey.values()) {
    const parent = node.parent ? byKey.get(node.parent) : undefined;
    if (parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  }
  // 根层按 BASIC → STANDARD → 业务类型 排序，子层保持 schema 声明序。
  const order: Record<SpgTypeKind, number> = {
    basic: 0,
    standard: 1,
    entity: 2,
    concept: 3,
    event: 4,
    index: 5,
    unknown: 6,
  };
  roots.sort((a, b) => order[a.kind] - order[b.kind] || a.key.localeCompare(b.key));
  return roots;
}

/** properties 按继承分离：详情树中默认只展示自有属性。 */
export function ownProperties(row: SpgTypeRow): SpgPropertyRow[] {
  return row.properties.filter((property) => !property.inherited);
}

/** Schema 关系行（M3.5 编辑：从 raw.relations 解析，编辑意图走 add/delete 列表）。 */
export interface SpgRelationRow {
  name: string;
  nameZh: string;
  desc: string;
  objectType: string;
  inherited: boolean;
}

export function parseRelations(raw: unknown): SpgRelationRow[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => {
      const row = record(item);
      const info = record(row.basicInfo);
      const predicate = record(info.name);
      const objectType = qualifiedName(record(record(row.objectTypeRef).basicInfo));
      return {
        name: text(predicate.name),
        nameZh: text(info.nameZh),
        desc: textOrNull(info.desc) ?? "",
        objectType,
        inherited: row.inherited === true,
      };
    })
    .filter((rel) => rel.name !== "");
}

// —— 图浏览（M3.4：reason DSL rows → 节点/边，2 列 id 查询即边）——

export interface GraphExplorerEdge {
  source: string;
  target: string;
}

export interface GraphExplorerData {
  nodes: string[];
  edges: GraphExplorerEdge[];
}

/** 2 列 id 的 rows → 去重节点 + 边（M3.3 实测：结果在 resultTableResult.rows）。 */
export function rowsToGraph(rows: unknown, limit = 200): GraphExplorerData {
  const nodes: string[] = [];
  const seen = new Set<string>();
  const edges: GraphExplorerEdge[] = [];
  const edgeKeys = new Set<string>();
  if (!Array.isArray(rows)) return { nodes, edges };
  for (const row of rows.slice(0, limit)) {
    if (!Array.isArray(row) || row.length < 2) continue;
    const source = text(row[0]);
    const target = text(row[1]);
    if (!source || !target) continue;
    for (const id of [source, target]) {
      if (!seen.has(id)) {
        seen.add(id);
        if (nodes.length < limit) nodes.push(id);
      }
    }
    const key = `${source}\u0000${target}`;
    if (!edgeKeys.has(key) && edges.length < limit) {
      edgeKeys.add(key);
      edges.push({ source, target });
    }
  }
  return { nodes, edges };
}

/** 图浏览查询结果（GET 无 body，POST 返回结构）。 */
export interface KagGraphQueryResult {
  status: string;
  header: string[];
  rows: unknown[];
  rowCount: number;
  truncated: boolean;
  error: string;
}

export function parseGraphQueryResult(raw: unknown): KagGraphQueryResult {
  const payload = record(raw);
  return {
    status: text(payload.status) || "UNKNOWN",
    header: Array.isArray(payload.header) ? payload.header.map((h) => text(h)) : [],
    rows: Array.isArray(payload.rows) ? payload.rows : [],
    rowCount: Number(payload.row_count ?? 0) || 0,
    truncated: payload.truncated === true,
    error: text(payload.error),
  };
}

// —— 图浏览增强（C1.1：节点详情/一跳图的 DSL 解析与构造）——
// 画布节点只有裸 id，点查/一跳需要类型——从执行过的 DSL 解析 MATCH 别名映射。
// DSL 按 M3.3 已验证契约形态构造（节点类型 namespace 全名、关系 label 裸名）；
// 本环境无图库，数据路径未实测（见计划文档 5.1 C1.6）。

const DSL_NODE_RE = /\(\s*(\w+)\s*:\s*([A-Za-z][\w.]*)\s*\)/g;
const DSL_REL_RE = /\[\s*\w+\s*:\s*([A-Za-z][\w.]*)\s*\]/;

/** 解析执行过的 DSL：MATCH 中别名 → 类型（如 `(n:m0ProbeLive.Creature)` → n）。 */
export function parseDslAliasTypes(dsl: string): Record<string, string> {
  const map: Record<string, string> = {};
  if (!dsl) return map;
  for (const match of dsl.matchAll(DSL_NODE_RE)) {
    const alias = match[1];
    if (alias && match[2] && map[alias] === undefined) map[alias] = match[2];
  }
  return map;
}

/** 首个关系 label（裸名，如 `-[p:workFor]->` → workFor）；无则空串。 */
export function parseDslRelationLabel(dsl: string): string {
  const match = dsl ? DSL_REL_RE.exec(dsl) : null;
  return match?.[1] ?? "";
}

/** 结果列名 → 别名（`n.id` → `n`；无点号/空返回空串）。 */
export function columnAlias(header: string): string {
  const dot = header.indexOf(".");
  return dot <= 0 ? "" : header.slice(0, dot);
}

export interface NodeTypeInfo {
  alias: string;
  type: string | null;
}

/**
 * 从执行结果构造 节点 id → {alias, type}（首次出现胜出）。
 * rows 为 2 列（source/target）；列别名由 header 解析、类型由 DSL 解析；
 * 别名或类型缺失时 type=null（详情面板提示不可用）。
 */
export function buildNodeTypes(
  dsl: string,
  header: string[],
  rows: unknown[],
): Map<string, NodeTypeInfo> {
  const aliasTypes = parseDslAliasTypes(dsl);
  const sourceAlias = header[0] ? columnAlias(header[0]) : "";
  const targetAlias = header[1] ? columnAlias(header[1]) : "";
  const map = new Map<string, NodeTypeInfo>();
  for (const row of rows) {
    if (!Array.isArray(row)) continue;
    const source = text(row[0]);
    const target = text(row[1]);
    if (source && !map.has(source)) {
      map.set(source, {
        alias: sourceAlias,
        type: sourceAlias ? aliasTypes[sourceAlias] ?? null : null,
      });
    }
    if (target && !map.has(target)) {
      map.set(target, {
        alias: targetAlias,
        type: targetAlias ? aliasTypes[targetAlias] ?? null : null,
      });
    }
  }
  return map;
}

/** 一跳 DSL（M3.3 契约形态；rel 空则无 label 关系）。评审 P2：节点 id 可能
 * 含单引号（图数据）——转义以保持 WHERE 语义稳定（只读 DSL，防语义截断）。
 * 契约注（2026-09-20 活图库实测）：reasoner 不支持关系 label 约束
 * （-[p:rel]->）——rel 恒传空串；label 分支保留仅为向后兼容、实际不触发。 */
export function oneHopDsl(type: string, rel: string, id: string): string {
  const relation = rel ? `-[p:${rel}]->` : `-[p]->`;
  const escaped = id.replace(/'/g, "\\'");
  return `MATCH (n:${type})${relation}(o) WHERE n.id = '${escaped}' RETURN n.id, o.id`;
}

/** 两跳 DSL（深化：跨节点的传递关系预览）。活图库实测（2026-09-20，M3BuildDemo）：
 * reasoner 仅允许起始节点带类型；关系 label 约束（-[p:rel]->）与目标/中间节点
 * 类型约束（(o:Type)）均不支持（SchemaException "Cannot find n"）。故中间/末端
 * 一律无类型变量，仅起始类型限定。 */
export function twoHopDsl(
  fromType: string,
  _midType: string,
  _toType: string,
): string {
  return `MATCH (n:${fromType})-[p1]->(x)-[p2]->(o) RETURN n.id, o.id`;
}

// —— 推理任务（GET /api/kag/tasks，A.3 契约）——

/** 构建任务 live 状态（P0a：节点级聚合；unknown=上游不可达/无此 job）。 */
export type KagBuildLiveStatus = "running" | "success" | "failed" | "pending" | "unknown";

export function parseBuildLiveStatus(raw: unknown): KagBuildLiveStatus {
  const value = text(raw);
  return value === "running" || value === "success" || value === "failed" || value === "pending"
    ? value
    : "unknown";
}

export interface KagTaskRow {
  taskId: string;
  /** 任务类型（M4-A）：inference（bridge 上报）或 build（构建任务）。 */
  kind: string;
  sessionId: string;
  projectId: string;
  namespace: string;
  question: string;
  answerDigest: string;
  costMs: number;
  references: string[];
  createdAt: string;
  /** P0a：build 记录实时状态（inference 行无此字段）。 */
  liveStatus?: KagBuildLiveStatus;
}

export function parseKagTasks(raw: unknown): KagTaskRow[] {
  const payload = record(raw);
  const rows = payload.tasks;
  if (!Array.isArray(rows)) return [];
  return rows.map((rawRow) => {
    const row = record(rawRow);
    const references = Array.isArray(row.references)
      ? row.references.map(text).filter(Boolean)
      : [];
    const costMs = Number(row.cost_ms);
    return {
      taskId: text(row.task_id),
      kind: text(row.kind) || "inference",
      sessionId: text(row.session_id),
      projectId: text(row.project_id),
      namespace: text(row.namespace),
      question: text(row.question),
      answerDigest: text(row.answer_digest),
      costMs: Number.isFinite(costMs) ? Math.max(0, Math.trunc(costMs)) : 0,
      references,
      createdAt: text(row.created_at),
      liveStatus: parseBuildLiveStatus(row.live_status),
    };
  });
}

// —— 构建任务可观测（P0a：项目构建列表 + 详情节点）——

/** 详情执行节点（name/type/status/traceLog；traceLog 服务端已截断 2k）。 */
export interface KagBuildNode {
  name: string;
  type: string;
  status: string;
  traceLog: string;
}

export interface KagBuildDetail {
  job: Record<string, unknown>;
  liveStatus: KagBuildLiveStatus;
  nodes: KagBuildNode[];
  /** M5 配置 executor（§8.9）：失败根因分类（executor_unconfigured / command_failed / null）。 */
  failureReason: "executor_unconfigured" | "command_failed" | null;
}

export function parseKagBuilds(
  raw: unknown,
): (KagTaskRow & { liveStatus: KagBuildLiveStatus })[] {
  const payload = record(raw);
  const rows = payload.builds;
  if (!Array.isArray(rows)) return [];
  const tasks = parseKagTasks({ tasks: rows });
  return rows.map((rawRow, index) => {
    const row = record(rawRow);
    return { ...tasks[index], liveStatus: parseBuildLiveStatus(row.live_status) };
  });
}

export function parseKagBuildDetail(raw: unknown): KagBuildDetail | null {
  const payload = record(raw);
  const job = record(payload.job);
  if (!job.id && !job.taskId) return null;
  const nodes = Array.isArray(payload.nodes)
    ? payload.nodes.map((rawNode) => {
        const node = record(rawNode);
        return {
          name: text(node.name),
          type: text(node.type),
          status: text(node.status),
          traceLog: text(node.trace_log),
        };
      })
    : [];
  const reason = text(payload.failure_reason);
  return {
    job,
    liveStatus: parseBuildLiveStatus(payload.live_status),
    nodes,
    failureReason:
      reason === "executor_unconfigured" || reason === "command_failed" ? reason : null,
  };
}

// —— 概念规则（C2：/projects/{id}/concept/rules 归一行）——
// 后端归一：reasoning 行 {kind:logical, subject_type/name, predicate,
// object_type/name, dsl}（TripleSemantic）；taxonomy 行 {kind:taxonomy,
// concept_name, dsl}（DynamicTaxonomySemantic）。宽松解析、缺字段安全缺省。

export interface KagConceptRule {
  kind: "logical" | "taxonomy";
  /** logical：subject 概念类型（限定全名）；taxonomy：空。 */
  subjectType: string;
  /** logical：subject 概念名；taxonomy：与 conceptName 同值。 */
  subjectName: string;
  predicate: string;
  /** logical：object 概念类型（限定全名）；taxonomy：空。 */
  objectType: string;
  objectName: string;
  /** taxonomy：概念名（belongTo 规则归属）；logical：subject 概念名。 */
  conceptName: string;
  dsl: string;
}

export interface KagConceptRules {
  typeName: string;
  reasoning: KagConceptRule[];
  taxonomy: KagConceptRule[];
  /** schema 中是否存在 belongTo 属性（实体类型→该概念类型）；定义门禁。 */
  belongToReady: boolean;
}

function parseConceptRule(raw: unknown): KagConceptRule | null {
  const row = record(raw);
  const kind = text(row.kind);
  if (kind === "taxonomy") {
    const conceptName = text(row.concept_name);
    if (!conceptName) return null;
    return {
      kind: "taxonomy",
      subjectType: "",
      subjectName: "",
      predicate: "belongTo",
      objectType: "",
      objectName: "",
      conceptName,
      dsl: text(row.dsl),
    };
  }
  if (kind !== "logical") return null;
  const subjectType = text(row.subject_type);
  if (!subjectType) return null;
  const subjectName = text(row.subject_name);
  return {
    kind: "logical",
    subjectType,
    subjectName,
    predicate: text(row.predicate) || "leadTo",
    objectType: text(row.object_type),
    objectName: text(row.object_name),
    conceptName: subjectName,
    dsl: text(row.dsl),
  };
}

export function parseConceptRules(raw: unknown): KagConceptRules {
  const payload = record(raw);
  const pick = (rows: unknown): KagConceptRule[] =>
    Array.isArray(rows)
      ? rows.map(parseConceptRule).filter((rule): rule is KagConceptRule => rule !== null)
      : [];
  return {
    typeName: text(payload.type_name),
    reasoning: pick(payload.reasoning),
    taxonomy: pick(payload.taxonomy),
    belongToReady: payload.belong_to_ready === true,
  };
}

// —— 构建命令占位符校验（阶段 B-1 评审 P2）——
// 导入模板含 <data-repo-url>/<commit-id> 等占位符，未替换即提交会送出
// 字面占位命令——提交前用该谓词拦截。占位符形如 <word>（内部无空白）；
// shell 重定向 `cat < input.txt > output.txt` 因尖括号间含空白不命中。

/** 命令中是否仍含未替换的模板占位符（<…>，内部无空白）。 */
export function hasKagCommandPlaceholder(command: string): boolean {
  return /<[^>\s]+>/.test(command);
}

// —— kag settings 域（GET/PUT /api/settings/kag；bridge_api_key write-only）——

export interface KagSettings {
  spgServerUrl: string;
  bridgeCommand: string;
  bridgeArgs: string[];
  kagProjectDir: string;
  namespace: string;
  projectId: string;
  /** 无用户上下文时 OpenSPG 归因（create_project 回落）。 */
  serviceUserNo: string;
  /** 响应恒不回显 key，只回显“已设置”标记。 */
  bridgeApiKeySet: boolean;
}

export function parseKagSettings(raw: unknown): KagSettings {
  const row = record(raw);
  const args = Array.isArray(row.bridge_args) ? row.bridge_args.map(text) : [];
  return {
    spgServerUrl: text(row.spg_server_url),
    bridgeCommand: text(row.bridge_command),
    bridgeArgs: args,
    kagProjectDir: text(row.kag_project_dir),
    namespace: text(row.namespace),
    projectId: text(row.project_id),
    serviceUserNo: text(row.service_user_no),
    bridgeApiKeySet: row.bridge_api_key_set === true,
  };
}

/** 可编辑草稿；bridgeApiKey 三态：null = 保留已存 key。 */
export interface KagSettingsDraft {
  spgServerUrl: string;
  bridgeCommand: string;
  bridgeArgsText: string;
  kagProjectDir: string;
  namespace: string;
  projectId: string;
  serviceUserNo: string;
  bridgeApiKey: string | null;
}

export function kagSettingsToDraft(settings: KagSettings): KagSettingsDraft {
  return {
    spgServerUrl: settings.spgServerUrl,
    bridgeCommand: settings.bridgeCommand,
    bridgeArgsText: settings.bridgeArgs.join("\n"),
    kagProjectDir: settings.kagProjectDir,
    namespace: settings.namespace,
    projectId: settings.projectId,
    serviceUserNo: settings.serviceUserNo,
    bridgeApiKey: null,
  };
}

/** PUT 请求体：空 key = 保留旧值（后端语义），args 按行拆分去空白。 */
export function kagDraftToRequest(draft: KagSettingsDraft): Record<string, unknown> {
  return {
    spg_server_url: draft.spgServerUrl.trim(),
    bridge_command: draft.bridgeCommand.trim(),
    bridge_args: draft.bridgeArgsText
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean),
    kag_project_dir: draft.kagProjectDir.trim(),
    namespace: draft.namespace.trim(),
    project_id: draft.projectId.trim(),
    service_user_no: draft.serviceUserNo.trim(),
    // null/空串都表示“保留已存值”（后端：空即沿用旧值）
    bridge_api_key: draft.bridgeApiKey == null ? "" : draft.bridgeApiKey,
  };
}

// —— 创建项目表单（POST /api/kag/projects）——

export interface KagProjectCreateForm {
  name: string;
  namespace: string;
  embeddingModelId: string;
  vectorDimensions: string;
  serviceUserNo: string;
}

export const EMPTY_PROJECT_CREATE_FORM: KagProjectCreateForm = {
  name: "",
  namespace: "",
  embeddingModelId: "",
  vectorDimensions: "",
  serviceUserNo: "",
};

const NAMESPACE_RE = /^[A-Za-z][A-Za-z0-9]{2,63}$/;
const USERNO_RE = /^[A-Za-z0-9_]{6,20}$/;

/** 校验失败返回 i18n 键（英文原文即键），通过则返回空数组。 */
export function validateProjectCreateForm(
  form: KagProjectCreateForm,
): string[] {
  const errors: string[] = [];
  if (!form.name.trim()) errors.push("Project name is required.");
  if (!NAMESPACE_RE.test(form.namespace.trim()))
    errors.push(
      "Namespace must be 3-64 characters, letters and digits only, starting with a letter.",
    );
  if (!form.embeddingModelId)
    errors.push("Select an embedding model for the project vectorizer.");
  if (form.vectorDimensions.trim()) {
    const value = Number(form.vectorDimensions.trim());
    if (!Number.isInteger(value) || value <= 0)
      errors.push("Vector dimensions must be a positive integer.");
  }
  if (form.serviceUserNo.trim() && !USERNO_RE.test(form.serviceUserNo.trim()))
    errors.push(
      "Service account must be 6-20 characters: letters, digits, or underscores.",
    );
  return errors;
}

export function projectCreateRequest(
  form: KagProjectCreateForm,
): Record<string, unknown> {
  const dimensions = Number(form.vectorDimensions.trim());
  const body: Record<string, unknown> = {
    name: form.name.trim(),
    namespace: form.namespace.trim(),
    embedding_model_id: form.embeddingModelId,
    service_user_no: form.serviceUserNo.trim(),
  };
  if (form.vectorDimensions.trim() && Number.isInteger(dimensions) && dimensions > 0)
    body.vector_dimensions = dimensions;
  return body;
}

// —— embedding 模型目录（/api/settings/catalog 的 services.embedding.profiles）——
// web 侧 Catalog 类型未列 embedding 服务（UI 未消费），这里按目录真实形态宽松解析。

export interface KagEmbeddingProfile {
  id: string;
  name: string;
  model: string;
}

export function parseEmbeddingProfiles(raw: unknown): KagEmbeddingProfile[] {
  const catalog = record(record(raw).catalog);
  const embedding = record(record(catalog.services).embedding);
  const profiles = embedding.profiles;
  if (!Array.isArray(profiles)) return [];
  return profiles
    .map((rawProfile) => {
      const profile = record(rawProfile);
      const first = Array.isArray(profile.models)
        ? record(profile.models[0])
        : {};
      return {
        id: text(profile.id),
        name: text(profile.name) || text(profile.id),
        model: text(first.model) || text(profile.model) || text(profile.id),
      };
    })
    .filter((profile) => profile.id !== "");
}
