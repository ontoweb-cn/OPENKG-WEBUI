# 知识中心 Phase 1a 实施任务清单（评审稿）

- 日期：2026-09-22
- 分支：`feature/knowledge-center`
- 依据：[../knowledge-center-port-design.md](../knowledge-center-port-design.md)（已定稿）§六 Phase 1 拆分后的 **1a 管理闭环**
- 流程：本清单评审通过后实施；每个任务组独立提交；Phase 1b / 1.5 / 2 各自再走"细化→评审→实施"

## 一、范围与非目标

**范围**：不触碰 turn 路径的最小管理闭环——知识库列表/创建/删除、文档上传（多文件）与解析进度（轮询）、文档管理、检索试玩、设置页合并、导航入口。

**非目标**（后续阶段）：chat 接线（1b）、composer 勾选与 B2 kb_ids 透传（1.5）、默认知识库 A3（1b）、zip/文件夹结构化上传 A4（Phase 2）、WS/SSE 日志流（Phase 2）、外部源（Phase 2+）、MCP server 配置 A5（1b，仅当含 CLI 后端）。

## 二、关键实现决策（评审重点）

| # | 决策 | 说明 |
| --- | --- | --- |
| D1 | **凭据与身份完全复用 agent-loop 体系** | 知识设置块不新增凭据字段（默认形状仅 `enabled`，预留可选 `base_url/api_key` 覆盖项备用）。代理调用 `resolve_backend_identity(profile, family="http", user_id=当前用户)`（`services/agent_loop/identity.py:431`，支持脱离 turn 路径按 user_id 解析）取 `api_key + X-Intellect-*` headers——天然满足 P1-1 同一身份源。未配置 intellect profile 或身份不可用时：设置页给出引导，`PRIMARY_NAV` 入口隐藏。连接状态/身份链接 UI 直接复用既有 `/api/settings/agent-loop/detect` 与 `/identity` 端点 |
| D2 | **创建流程不移植 DeepMentor 的 connect/probe** | DeepMentor 的 Intellect RAG 是"连接外部数据集"型 KB（probe/connect + server_url/api_key/dataset_id）；本方案 KB 是 rag-app **原生 dataset**，服务地址与凭据来自 agent-loop profile——创建表单简化为 name/description/可见范围说明，无连接向导 |
| D3 | **检索试玩是新增面** | DeepMentor 前端无独立检索端点（检索绑定聊天）。Phase 1a 新建试玩面板：单 dataset 检索，调 rag-app `POST /datasets/{id}/search`（经代理），展示 chunks/highlight/score |
| D4 | **代理路径镜像 rag-app REST（thin）** | 决策 Q1：`/api/knowledge/*` 与上游 `/api/v1/*` 一一对应，形状翻译在前端 parse 层（`web/features/knowledge/api.ts`），不在代理层做模型重组 |
| D5 | **进度采用 DeepMentor 的列表快照轮询模式** | 活跃期每 4s 轮询 KB 列表 + 文档列表（文档状态含 `run/progress/chunk_count`），日志按需拉 `ingestions`；不做 WS/SSE（决策 Q4） |

## 三、API 契约（T0 已核对，2026-09-22）

上游响应均为 RagFlow 派生信封 `{code, data, message}`（`code=0` 成功）；代理原样透传信封，前端 parse 层解包。

| 用途 | openkg-webui 代理 | rag-app 上游（已核对路径） | 关键参数/返回 |
| --- | --- | --- | --- |
| KB 列表 | GET `/api/knowledge/datasets` | GET `/api/v1/datasets`（dataset_api.py:367） | query：`page(1)/page_size(30)/orderby/desc/id/name`；data 为数组，项含 `id/name/description/document_count/chunk_count/token_count/permission/...` |
| 创建 KB | POST `/api/knowledge/datasets` | POST `/api/v1/datasets`（:82） | body：`name`(必填)、`description?`、`permission: "me"\|"team"`（对应 §十一-5 可见范围）、`chunk_method?`(默认 naive)、`parser_config?`、`embedding_model?`；ownership 由上游按 X-Intellect-* 注入 |
| KB 详情/删除 | GET/DELETE `/api/knowledge/datasets/{id}` | GET/DELETE `/api/v1/datasets/{id}`（:511/:234） | — |
| 文档列表 | GET `/api/knowledge/datasets/{id}/documents` | GET `/api/v1/datasets/{id}/documents`（document_api.py:794） | query：`page/page_size/orderby/desc/keywords?/create_time_from?/create_time_to?`；data：`{docs:[{id/name/run/progress/chunk_count/token_count/size/type/...}], total}` |
| 上传（多文件） | POST `/api/knowledge/datasets/{id}/documents` | POST `/api/v1/datasets/{id}/documents`（:371） | multipart：`files[]`（多文件）+ form `type=local`、可选 `parent_path`（存储前缀）；上传后自动触发解析 |
| 文档删除（批量） | DELETE `/api/knowledge/datasets/{id}/documents` | DELETE `/api/v1/datasets/{id}/documents`（:1198） | body：`{ids: [...]}` |
| 解析触发/停止 | POST `.../documents/parse`、`.../documents/stop` | POST `/api/v1/datasets/{id}/documents/parse`（:1611）、`.../stop`（:1725） | body：`{ids: [...]}` |
| 文档详情 | GET `/api/knowledge/datasets/{id}/documents/{doc_id}` | GET `/api/v1/datasets/{id}/documents/{document_id}`（:2150） | — |
| 摄取记录/日志 | GET `/api/knowledge/datasets/{id}/ingestions[/{log_id}]` | GET `/api/v1/datasets/{id}/ingestions`（dataset_api.py:804，`log_type=dataset\|file`、`page/page_size≤100`）、`/ingestions/<log_id>`（:830，含 `dsl` 全量） | logs 项含 `progress/progress_msg/operation_status/process_duration/document_name/task_type` |
| 文档预览/缩略图 | GET `/api/knowledge/documents/{doc_id}/preview`、GET `/api/knowledge/thumbnails` | GET `/api/v1/documents/{doc_id}/preview`（document_api.py:2106）、GET `/api/v1/thumbnails`（:1371，query `doc_id`） | 字节流透传 |
| 检索试玩（单库） | POST `/api/knowledge/datasets/{id}/search` | POST `/api/v1/datasets/{id}/search`（dataset_api.py:649） | body `SearchDatasetReq`：`question`(必填)、`page/size`、`top_k(≤1024)`、`similarity_threshold(0)`、`vector_similarity_weight(0.3)`、`use_kg`、`keyword`、`doc_ids?`、`rerank_id?`、`meta_data_filter?` |
| 设置块 | GET/PUT `/api/settings/knowledge` | — | `{enabled, base_url, api_key(掩码三态)}`；见 D1 修订（下） |

**D1 实施修订**（agent-loop profile 指向 intellect-team 服务而非 rag-app，故）：`base_url`/`api_key` 为**必配项**（api_key 取值 = team 部署的 `INTELLECT_RAG_API_KEY` 同一把，§十一-2）；身份 header 仍经 `resolve_backend_identity()` 按当前用户解析——token 模式下 Bearer 直接用该用户 member token（identity.api_key），header 模式下 Bearer 用知识设置的服务 key + `X-Intellect-User` 归因。

rag-app 侧 1a **无新增业务端点**（A3 在 1b、A4 在 Phase 2），仅 T1 的 OpenAPI 登记。

## 四、任务清单

| # | 任务 | 落点 | 验收 | 估力 |
| --- | --- | --- | --- | --- |
| T0 | 契约清单定稿：按 §三 表逐端点核对上游参数/返回形状（读 rag-app handler），把"复用/facade"与请求/响应关键字段写回本文档 | 本文档 §三 | 清单与上游代码一致；评审通过 | S |
| T1 | rag-app A2：`api/openapi/spec.py` 按 T0 清单登记路径（照抄现有 `spec.path()` 模式，唯一 guard 已有） | intellect-rag-app | `GET /api/v1/openapi.json` 覆盖全部消费路径；AgentUI typegen 不破坏 | S |
| T2 | knowledge 设置块：`runtime_settings.py` 加 `DEFAULT_SYSTEM_SETTINGS["knowledge"]` + `_normalize_knowledge()`（形状：`{enabled: bool, base_url?: str, api_key?: str(掩码)}`）并入 `_normalize_system`；`routers/settings.py` 加 GET/PUT `/api/settings/knowledge`（掩码/省略即保留/same-origin，照抄 kag）；落盘 `data/user/settings/knowledge.json` | openkg-webui | pytest 覆盖归一化与掩码语义；PUT 省略 `api_key` 不清空 | S-M |
| T3 | 知识代理路由：`openkg_webui/api/routers/knowledge.py`——§三 全部端点；挂载 `prefix="/api/knowledge", dependencies=_auth`（`api/main.py`）；写端点 `_require_same_origin`；身份注入：读 agent-loop 主 profile → `resolve_backend_identity(..., user_id=当前用户)` → 上游 `Authorization + X-Intellect-*`；上游错误 502 截断；**上传用 httpx 流式转发 multipart**（不整体读入内存）；operationId 唯一 | openkg-webui | pytest：鉴权门、same-origin、502 归一、身份 header 注入（mock 上游）；身份不可用返回 409+引导码 | M |
| T4 | 契约产物：`python scripts/export_frontend_contracts.py` → `cd web && npm run contracts:generate`，提交 `web/contracts/schema/` 与 `generated/` | openkg-webui | 两侧 `--check` 过（CI 门禁） | S |
| T5 | 前端 feature 骨架：`web/features/knowledge/{model/,api.ts}`——用生成契约类型写 parse 函数（rag-app 形状 → 裁剪版 UI 模型，对齐 DeepMentor `KnowledgeBaseSummary`/`ProgressInfo` 形状以便组件少改） | openkg-webui | 单测：parse 函数对样例 payload | M |
| T6 | 页面与导航：`web/app/(utility)/knowledge/page.tsx`（+ 可选 `/knowledge/[datasetId]`）；`nav-entries.ts` `PRIMARY_NAV` 加"知识"入口；未启用/身份不可用时入口与页面隐藏并给设置引导 | openkg-webui | 未配置 → 入口不渲染；子路径部署可用 | S-M |
| T7 | 页面组件移植（裁剪）：KB 列表卡片 + 状态徽标（`KbStatusBadge`）、创建表单（D2 简化）、详情页——文档 tab（列表/上传 `FileDropZone` 多文件/删除/预览 `KbFilePreview`）+ 进度轮询（D5）+ 检索试玩面板（D3）；移植文件头标 `Derived from DeepMentor <path>, modified`（§十义务） | openkg-webui | 上传→进度→文档状态→试玩全链路手动验收；lint/i18n 门禁过 | **L** |
| T8 | 设置页合并（Q5）：`/settings/knowledge/page.tsx` 重构为 `KnowledgeCenterSettingsSection`（enabled 开关、连接检测、身份链接状态、设置引导——复用 agent-loop detect/identity 端点）+ 保留 `DocumentParsingSettingsSection` 原组件于下方；`settings-nav.ts` knowledge 条目 label/blurb 更新（"知识中心"）；`EXTENSION_ENDPOINTS` 按需登记 | openkg-webui | 文档解析设置功能不回归；KC 设置即改即存 | S-M |
| T9 | i18n 迁移：从 DeepMentor `web/locales/{en,zh}/app.json` 抽取知识域 key → 本项目命名空间（建议 `knowledge.*`），补齐新增文案；en/zh parity 门禁过 | openkg-webui | `i18n:check` 过；无字面量 UI 文本（ESLint 门禁） | M |
| T10 | 质量门禁收尾：`route_budgets` 登记新页面；depcruise 分层；后端 pytest 全量 | openkg-webui | 全部门禁绿 | S |

## 五、实施顺序与提交切分

```
T0（评审点：契约清单定稿）
  → T1（rag-app 仓库单独提交）
  → T2 → T3 → T4（openkg-webui 后端三个提交）
  → T5 → T6+T7 → T8 → T9+T10（前端四个提交）
```
T7 内部可再按 列表/创建 → 详情/上传/进度 → 试玩 三次提交。设计文档与本任务清单在评审通过后的首个提交入库。

## 六、Phase 1a 验收清单

1. 上传（多文件）→ 进度轮询 → 文档状态更新 → 检索试玩出结果，全程同一浏览器会话。
2. 用户隔离：用户 A 建的库对用户 B 不可见（P1-1 用例①的 UI 侧；turn 侧留给 1b）。
3. 未启用/未链接身份：入口隐藏，设置页有引导，无失效入口。
4. 子路径部署（`NEXT_PUBLIC_BASE_PATH`）下全功能可用。
5. 门禁全绿：contracts 双侧 `--check`、`i18n:check`、ESLint（含 no-literal-ui-text）、route budget、后端 pytest、rag-app 侧测试。
6. 署名义务落实：移植文件头 + 仓库第三方声明（§十）。

## 七、风险

- **multipart 流式转发**（T3）：FastAPI→httpx 转发大文件需 `stream=True` 逐块中继，KAG 代理无此先例（纯 JSON）——先做 spike 验证内存占用。
- **operationId 冲突**（T3/T4）：导出脚本对重复 operationId 直接 RuntimeError，命名需按 `knowledge_<resource>_<action>` 规范。
- **rag-app 文档列表/上传端点的真实参数形状**（T0/T5）：调研基于 handler 阅读，T0 逐个核对时如与 §三 有出入，以 T0 修订为准。
- **组件移植量**（T7）：`CreateKbModal`（1864 行）裁剪为单引擎表单预计缩至数百行；`KnowledgeHome` 双 tab 结构裁为单 tab，引用链需仔细清理。
