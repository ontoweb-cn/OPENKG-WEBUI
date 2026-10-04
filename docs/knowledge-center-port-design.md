# 知识中心移植方案（DeepMentor → OPENKG-WebUI / intellect-rag-app）

- 分析日期：2026-09-22
- 移植来源：`~/projects/DeepMentor`（知识中心 = 多引擎 RAG 知识库管理）
- 宿主：`~/projects/openkg-webui`（UI + agent loop 接线）；后端候选：`~/projects/intellect-rag-app`
- 状态：方案已定稿（2026-09-22 决策 Q1-Q8，见 §七），待实施；基于静态代码调研，未做运行时联调验证
- 关联文档：[agentui-kagweb-merge-feasibility.md](agentui-kagweb-merge-feasibility.md)（已定调"第一阶段继续使用外部 Intellect RAG，由 WebUI 加服务适配层"）、[kag-integration-design.md](kag-integration-design.md)（agent loop 插件式接入先例）

## 一、结论先行

1. **UI 移植可行且成本可控。** DeepMentor 前端与本项目同为 Next.js 16 + React 19 + react-i18next，知识域高度自包含（约 7000 行组件 + 3 个 hooks + 领域 API 模块），仅依赖 `apiUrl()/wsUrl()` 子路径辅助——与本项目 `web/shared/api/client.ts` 同构，移植以改造为主、重写为辅。
2. **后端放 intellect-rag-app 可行，且优于整体移植 DeepMentor 后端。** intellect-rag-app 现有 REST（约 270 条路由）已覆盖知识中心后端核心：dataset CRUD、文档全生命周期（上传/解析/停止/进度/预览/批量）、chunk 管理、hybrid 检索、多租户 ownership/visibility。DeepMentor 后端约 1.5 万行的真正增量价值在"多引擎门面 + 连接型知识库 + 外部源同步"，这些按当前思路（暂只支持 intellect-rag）Phase 1 都不需要。
3. **暂只支持 intellect-rag 时，功能足够支撑第一版管理闭环**（列表/创建/上传/索引进度/文档管理/检索），缺口集中在四点：OpenAPI 契约覆盖、结构化上传（文件夹/zip）、外部源同步、默认知识库——均为小改或可延后（详见 §四）。
4. **Agent loop 接入三条通道均已在本项目生产验证**（KAG 先例）：intellect-team 走"prompt grounding 块 + Intellect 栈原生 MCP"（即 Plugin 方式）；CLI 后端走 session workdir `.mcp.json` 注入；其它 HTTP 后端无注入通道，由各服务自行注册 intellect MCP server + prompt 块。

## 二、调研摘要

### 2.1 DeepMentor 知识中心盘点

| 模块 | 前端 | 后端 | 耦合度 |
| --- | --- | --- | --- |
| KB 管理（CRUD/默认/配置/重索引/重试） | `web/components/knowledge/{KnowledgePage,KnowledgeHome,KnowledgeBaseDetail,CreateKbModal}.tsx`、`web/hooks/useKnowledgeBases.ts`、`web/features/knowledge/api/` | `deepmentor/api/routers/knowledge.py`（4864 行 76 端点）、`deepmentor/knowledge/{manager,initializer,add_documents}.py` | 中（经 knowledge_access 走 auth/tenant） |
| 上传/文件管理 | `FileDropZone/KbDocumentList/KbFilesTab/KbFilePreview` | knowledge.py upload/files/folders 路由 | 低-中 |
| 进度/任务流 | `web/hooks/useKnowledgeProgress.ts`（REST 轮询 + WS + SSE 日志） | `knowledge/progress_tracker.py` + 进程内广播器 | 中 |
| 检索引擎（默认 LlamaIndex + 12 种远程/本地引擎） | `web/features/knowledge/api/engines.ts` + 各引擎表单 | `deepmentor/services/rag/pipelines/*` | 低（每 pipeline 自包含） |
| 外部源（GitHub/Web/本地夹同步） | `KbGitHubSourcesSection/KbWebSourcesSection` | `services/{github_source,web_source}/` | 低 |
| 连接型 KB（Obsidian/MarginNote4/IMA/Subagent） | composer 勾选 | `capabilities/{obsidian,ima,marginnote4,subagent}/` | **高**（绑定 DeepMentor capability/orchestrator，不移植） |
| RAG↔聊天集成（rag 工具、manifest 注入） | `features/chat/controllers/buildStartTurnInput.ts` | `tools/rag_tool.py`、`agents/chat/agentic_pipeline.py`、`knowledge/manifest.py` | **高**（不移植，只借鉴接口形状） |
| 多用户/权限 | — | `multi_user/knowledge_access.py`（admin/user 双工作区 + grant） | 高（集中在可替换一层） |

关键事实：**DeepMentor 已有 Intellect RAG 远程引擎**（`KbIntellectRagSection.tsx`、`probe/connect-intellect-rag` 端点、`services/rag/pipelines/intellect_rag/client.py` 直连 RagFlow 派生 REST `/api/v1/retrieval`、`/api/v1/datasets`）——UI 与接口形状都有现成参照。存储为纯文件系统、无 ORM；与宿主的耦合点集中在 auth、`multi_user/knowledge_access.py`、runtime settings、进度广播器四处，接口形状 `RAGService.search() → {query, answer, content, sources[], provider}` 可直接借鉴。

### 2.2 intellect-rag-app 能力与缺口

定位：Intellect RAG 生态交付层（Quart API :9380 + Flask Admin :9381 + MCP :9382 + Task Executor），引擎在 intellect-rag，跨仓库 PEP 420 `api` 命名空间。

已有（与知识中心直接相关）：
- 知识库：`api/apps/restful_apis/dataset_api.py` — CRUD、标签、元数据 schema、摄取记录、GraphRAG/RAPTOR/Mindmap 索引任务、embedding 切换
- 文档：`document_api.py`（约 2200 行）— 上传（multipart/URL，SSRF 防护）、解析/停止、进度、预览/缩略图、批量状态、元数据
- 分块与检索：`chunk_api.py` CRUD + `POST /retrieval` hybrid 检索（scope 权限、metadata 过滤、KG/TOC 增强、rerank）
- 文件树与版本：`file_api.py`、`file_commit_api.py`（类 git 语义）
- 多租户：X-Intellect-* 身份协议、imt_ TEAM token、KB ownership/visibility（owner_user_id/team_id/project_id）、`KnowledgebaseService.accessible()`
- MCP：`mcp/server/server.py` 暴露 `intellect_retrieval`（streamable-http + SSE，self-host/host 两种鉴权，反向调用本机 REST）
- 模型管理：provider/models/llm API（可承接引擎设置控制面）

缺口（承载知识中心后端视角）：
1. OpenAPI 仅手工注册 5 组路径（`api/openapi/spec.py`），做 Web UI 契约需大量补齐
2. 无文件夹/zip 结构化上传语义（单文件/URL 上传已有，虚拟文件树已有）
3. 无 GitHub/Web 源同步（connector 仅 Google Drive/Gmail/Box/RSS）
4. 无"默认知识库"概念
5. Admin（:9381）只有运维面（用户/角色/监控），无知识库管理端点；仓库自身无前端（WebUI 在兄弟项目 intellect-webui）
6. 与 openkg-webui 是两套身份体系，需桥接

### 2.3 本项目接入点（KAG 先例全程可抄）

| 接入点 | 落点 | 模板 |
| --- | --- | --- |
| 页面路由 | `web/app/(utility)/knowledge/**`（自动获得 AppShell/登录门/CapabilityGate） | `web/app/(utility)/kag/**` |
| 侧栏入口 | `web/components/sidebar/nav-entries.ts` 的 `PRIMARY_NAV`（与 Chat/Space 同层，决策 Q5） | — |
| Feature 模块 | `web/features/knowledge/{model/, api.ts, components/}` | `web/features/kag/` |
| REST 代理路由 | `openkg_webui/api/routers/knowledge.py` + `api/main.py` 挂 `/api/knowledge`（带登录依赖），写端点 `_require_same_origin`，上游错误 502 截断，凭据掩码 | `routers/kag.py` + `services/kag/openspg_client.py` |
| 契约 | `web/contracts/schema/openapi.json` → `npm run contracts:generate`（CI 硬门禁） | 既有管线 |
| 设置块 | `runtime_settings.py` 加 `knowledge` 块（`DEFAULT_SYSTEM_SETTINGS` + `_normalize_knowledge`，照抄 `_normalize_kag`）；`routers/settings.py` 加 GET/PUT（掩码/省略即保留/same-origin）；前端 `web/lib/settings-extensions.ts` 登记 + `settings-nav.ts` 加类目 | kag 块全链路 |
| prompt 注入 | `capabilities/chat/capability.py` `_build_request` blocks 列表（`kag_grounding_block` 先例，:716-725） | `services/kag/__init__.py:51` |
| CLI 后端 MCP 注入 | session workdir `.mcp.json` + `.claude/settings.json` 权限放行（`ensure_session_mcp_config` 先例，:104-168，接线在 capability.py:748-758） | 同上 |

注意：前端**不能直连**外部服务 baseURL（Next rewrite 只认 `isBackendPath()` 同源路径，CORS 白名单在 FastAPI 侧），必须走同源代理路由；`/settings/knowledge` 路由已被"文档解析"设置占用——决策 Q5：知识中心设置接管 `/settings/knowledge`，文档解析设置并入该页。

## 三、总体架构（推荐方案）

```
Web UI (openkg-webui, Next.js)
  web/features/knowledge/*   ← 移植自 DeepMentor，裁剪为单引擎（intellect-rag）
        │  同源相对路径 /api/knowledge/*（apiUrl()）
        ▼
openkg-webui (FastAPI)
  api/routers/knowledge.py   ← 薄代理：登录门 + same-origin guard + 身份注入 + 凭据掩码
  services/knowledge/        ← 上游 client + 设置读取 + manifest 组装 + MCP 配置下发
        │  httpx + Bearer/X-Intellect-*（复用 agent_loop identity 体系）
        ▼
intellect-rag-app (Quart :9380)  ← 知识中心后端归属地
  既有 /api/v1/datasets | documents | chunks | retrieval | files | ingestions
  + 新增 knowledge-center facade 端点（只补缺口，见 §四）
        │
        ├─ MCP server (:9382)  intellect_retrieval
        │     ← intellect-team 原生接入（Plugin 方式）
        │     ← CLI 后端经 session .mcp.json 接入
        │     ← 其它 agent loop 由各服务自行注册
        └─ intellect-rag 引擎（Task Executor / docStore / 对象存储）
```

分工原则：

- **openkg-webui 不长知识域逻辑。** 代理路由只做鉴权、身份映射、错误归一；知识库的权威数据永远在 intellect-rag-app（呼应 merge-feasibility §5.3"数据映射而非覆盖"）。
- **API 契约归属 intellect-rag-app。** 优先直接复用其既有 REST；确有缺口的端点在其仓库新增 facade blueprint（挂在 `api/apps/restful_apis/` 既有扫描机制下），使 Web UI 依赖一个稳定契约，而不是 270 条内部路由的全集。
- **引擎抽象只保留 `engine` 字段 + 轻量目录注册。** KB 元数据从第一天带 `engine` 字段（现在恒为 `intellect-rag`）；UI 只保留目录注册（名称/图标/描述），**引擎表单壳不移植**（决策 Q3）：`CreateKbModal` 裁剪为单引擎表单。未来加引擎 = intellect-rag-app facade 加 provider + 目录注册加条目 + 届时再引表单分发，数据无需迁移。

### 鉴权桥接（需讨论，见 §七-Q2）

推荐起步方案：**服务凭据 + 用户归因**——openkg-webui 代理路由用服务级 token（`INTELLECT_RAG_API_KEY` 模式）调 intellect-rag-app，附 `X-Intellect-User`（复用 `services/agent_loop/identity.py` 既有解析与 `identity_store.py` 凭据存放约定）。备选：每用户 imt_ token 透传（隔离更强，但要求用户在 intellect 侧都有账户且 token 生命周期管理更重）。intellect-rag-app 侧开启条件已核实（2026-09-22 二轮调研，详见 §九-9.1 A1）：`AUTH_RAG_SERVICE` 的唯一开关是 env `INTELLECT_RAG_API_KEY` 非空（`api/apps/__init__.py:285-290`），不要求 GATEWAY_MODE（修正本节初稿表述）；`X-Intellect-User` 无匹配账户时幂等自动建户（`ensure_team_user`）。安全注记：该路径直接信任 `X-Intellect-Role` header，代理只转发从可信会话派生的身份 header。

## 四、"暂只支持 intellect-rag"功能充分性评估

对照 DeepMentor 功能逐项处置：

| DeepMentor 功能 | Phase 1 处置 | 说明 |
| --- | --- | --- |
| KB 列表/创建/删除/详情/配置 | ✅ 直接复用 datasets API | UI 移植 + 代理转发 |
| 上传（多文件） | ✅ 直接复用 documents upload | — |
| 上传（文件夹保留结构 / zip） | ⚠️ Phase 1 逐文件循环上传，结构化语义 Phase 2 | intellect-rag-app 有虚拟文件树（files API），补批量/结构化端点属小改 |
| 文件列表/预览/下载/删除/移动 | ✅ documents + files API 已有 | — |
| 索引进度 | ✅ Phase 1 用 ingestions REST 轮询 | `useKnowledgeProgress` 裁剪为纯轮询；WS/SSE 日志流 Phase 2 |
| 重索引/失败重试 | ✅ datasets index 任务 + 文档重解析 | DeepMentor 的 version-N 版本目录语义不移植（引擎侧已有任务检查点/回滚） |
| 检索试玩/引用展示 | ✅ `POST /retrieval`（highlight、rerank、KG 开关齐全） | 引用形状对齐 DeepMentor `{query, answer, sources[]}` 便于复用引用卡组件 |
| 默认知识库 | ⚠️ facade 补一个 per-user 端点（或 Phase 1 前端存储） | 小改 |
| 外部源（GitHub/Web/本地夹） | ⛔ Phase 2+，建议实现为 intellect-rag-app connector | DeepMentor 的 `services/{github_source,web_source}/` 可作移植素材 |
| 连接型 KB（Obsidian/MarginNote4/IMA/WeKnora/LightRAG Server/Subagent） | ⛔ 不移植 | 深度绑定 DeepMentor capability/orchestrator，且与"知识引擎可插拔"目标正交 |
| 多引擎本体（llamaindex/graphrag/lightrag/pageindex） | ⛔ 不移植引擎代码 | intellect-rag 已内置 GraphRAG/RAPTOR/Mindmap；本地 llamaindex 引擎如有需求走 facade provider 接口（Phase 3） |
| 引擎设置控制面（preflight/凭证/model options） | ✅ 以 intellect-rag-app provider/models API 替代 | Settings 知识中心页只配：服务 base URL、凭据、连接测试 |
| manifest 注入（"库里有几份文档"免工具回答） | ✅ openkg-webui 侧组装 | 参考 `deepmentor/knowledge/manifest.py` 形状与 20 条上限，用 documents API 拉清单渲染 prompt 块 |
| 聊天中 rag 工具调用 + 引用卡 | ✅ 走 agent loop 既有事件映射 | runs 通道 tool_call/tool_result 已映射；CLI 走各自 MCP 帧展示 |
| 多用户隔离 | ✅ 两边都有 | openkg-webui 用户 ↔ intellect 用户/租户映射复用 agent loop identity 规则 |

结论：**后端功能足够**，Phase 1 需在 intellect-rag-app 补的只有小端点（结构化上传可选、默认知识库）+ OpenAPI 契约补齐；其余缺口都是主动裁剪而非能力缺失。

## 五、Agent Loop 接入矩阵

设计原则沿用 KAG 集成 §1："知识引擎的推理以插件形式接入 Agent Loop"。本项目 turn 契约没有工具面（"There is no tool layer"），所以检索必须发生在 agent loop 后端一侧或其可及的 MCP server 上：

| Agent Loop | 通道 | 具体做法 |
| --- | --- | --- |
| **intellect-team**（HTTP runs） | **Plugin 方式 = 自带 RAGProvider 插件（已核实，推荐主路径）** | intellect-team 已捆绑指向 intellect-rag-app 的 `plugins/rag/intellect-rag/` provider：RAGHttpClient 直连 `/api/v1`、每请求注入 X-Intellect-* 身份 header、自带 `intellect_search(kb_ids)` 等工具与每 turn prefetch/system_prompt 注入。接入 = 配置启用（§九-9.2 B1）+ run 级 kb_ids 透传（B2，两端各一处小改）。openkg-webui **不再**为其注入 manifest 块（provider 已做，避免双重注入）；grounding 块的主战场移至 CLI 后端 |
| **CLI 后端**（claude-code / codex / opencode） | **MCP（session 级注入）** | 复制 `ensure_session_mcp_config`：每 turn 在 session workdir 写 `.mcp.json`（指向 intellect MCP server 的 streamable-http URL + 凭据）+ `.claude/settings.json` 权限放行（`mcp__<server>__intellect_retrieval`）。凭据已定（决策 Q6）：host 模式 + 从 identity_store 取该用户 token 写入本会话 `.mcp.json`（与 agent loop 同一身份源），无需签发端点 |
| **其它后端**（hermes 本地 ACP / agentscope / custom-http） | **MCP（服务侧自注册）** | openkg-webui 无工具注入通道：只注入 prompt grounding 块；由各服务运营者将其自身 MCP 客户端指向 intellect MCP server（部署文档 + settings 校验提示） |

配套约定：
- 会话勾选 KB 沿用 DeepMentor 交互（composer 勾选 → `session.preferences.knowledge_bases`），但落 openkg-webui 自己的会话偏好；dataset_id 映射由代理层解析，不把 intellect 内部 ID 直接暴露为会话标识（merge-feasibility §5.3）。
- grounding 块失败不阻塞 turn（kag_block 先例：服务异常时置空继续）。
- MCP server 目前只有 1 个检索工具；Phase 2 可按需加 `intellect_list_documents`（支撑 kb_files 类问题），属 intellect-rag-app 侧增量。
- intellect-team 的 runs 事件**没有** `tool.failed`——工具失败以 `tool.completed` + `error:true` 表达（其 api_server adapter 实现约定），`RunsAgentLoopBackend` 事件映射需按此归一（§九-9.2 集成注记）。

## 六、实施计划

### Phase 0 — 前置确认（先于编码）
- ~~DeepMentor 代码权属/许可确认~~ 已完成：Apache-2.0（与本项目同许可），可移植，附署名义务（§十）
- ~~intellect-rag-app 部署形态确认~~ 已核实：`INTELLECT_RAG_API_KEY` env 即可启用服务间鉴权，GATEWAY_MODE 非必需（§九-9.1 A1）；~~intellect-team 挂载机制~~ 已确认：自带 RAGProvider 插件（§九-9.2）
- intellect-team 实例配置核对：目标实例是否显式配置过 `platform_toolsets.api_server`（决定 `rag` toolset 需否显式加入，§九-9.2 B1）
- API 契约草案：UI 所需端点清单 → 标注"直接复用 / facade 新增"

### Phase 1 — 最小管理闭环（对齐 merge-feasibility 阶段 3 的验收）
1. **intellect-rag-app**：OpenAPI 补齐知识中心相关路径；新增缺口端点（默认知识库；结构化上传视 Phase 1 体验决定是否做）
2. **openkg-webui 后端**：`knowledge` 设置块全链路（runtime_settings + `/api/settings/knowledge` + settings-extensions）；`/api/knowledge` 代理路由（登录门 + same-origin + 身份注入 + 掩码 + 502 归一）；contracts 生成
3. **openkg-webui 前端**：移植裁剪版知识中心——列表/创建（单引擎表单）/详情（文档 tab + 进度轮询 + 检索试玩）；导航挂 `PRIMARY_NAV`（决策 Q5）；设置接管 `/settings/knowledge` 并将文档解析设置并入该页（决策 Q5）；i18n 中英键迁移（DeepMentor 的 key 混在其全局 app.json 里，需搬运）
4. **agent loop 接线**：intellect-team 启用 RAGProvider 插件（配置，§九-9.2 B1）+ grounding 块（仅 CLI 后端需要）+ CLI `.mcp.json` 注入；若 Phase 1 不含 CLI 后端，MCP server（§九-9.1 A5）整体延后
5. 验收：上传 → 索引进度 → 检索 → 聊天中引用，全程用户隔离、子路径部署可回放

### Phase 2 — 体验补齐
结构化上传（文件夹/zip）、进度 WS/日志流、外部源 connector（GitHub/Web）、默认知识库打磨、引用卡样式对齐、MCP 增补文档清单工具

### Phase 3 — 多引擎（按需）
facade 定义 `KnowledgeEngine` provider 接口；接入第二个引擎（候选：本地 llamaindex 或 GraphRAG 直连）验证抽象；UI 引擎目录扩条目

### 风险
- **契约面大**：270 条内部路由 vs 5 组 OpenAPI——facade 端点清单要克制，避免把 Web UI 变成 intellect-rag 全量管理台
- **身份映射**：openkg-webui 用户在 intellect 侧不存在的首访建户语义（其 `AUTH_RAG_SERVICE` 路径已有 auto-provision）需与 agent loop identity 行为对齐，避免两套建户逻辑
- **UI 移植量**：`CreateKbModal` 1864 行、`useKnowledgeProgress` 486 行是两个大头，裁剪后预计仍需数周级前端工作
- **i18n 搬运**：知识域 key 在 DeepMentor 全局 app.json 中未隔离，搬运需逐键梳理（ESLint `no-literal-ui-text` + i18n parity 门禁会强制做对）

## 七、决策记录（2026-09-22 定稿）

| # | 决策点 | 决策 |
| --- | --- | --- |
| Q1 | 契约策略 | **复用既有 REST + 最小 facade**；翻译成本落在 UI 移植的 parse 函数层（§八 P3 修正后的表述），Phase 0 交付契约清单不变 |
| Q2 | 鉴权模式 | **服务 token + `X-Intellect-User` 归因**起步；须复用 team 既有同一把 key（§十一-2），并以 P1-1 两条用例为验收 |
| Q3 | UI 裁剪幅度 | **保留 `engine` 字段 + 轻量目录注册（名称/图标/描述），不保留引擎表单壳**；`CreateKbModal` 裁剪为单引擎表单，第二引擎落地时再引表单分发（§八 P3 修正后的推荐） |
| Q4 | 进度机制 | **Phase 1 纯轮询**（ingestions REST，含 `progress_msg` 日志文本）；WS/SSE 日志流 Phase 2 |
| Q5 | 导航与设置 | **入口放 `PRIMARY_NAV`**；**`/settings/knowledge` 合并**——知识中心设置接管该路由，现有文档解析设置并入该页，不新增 `/settings/knowledge-center` |
| Q6 | CLI 后端 MCP 凭据 | **host 模式 + 从 identity_store 取该用户 token 写入本会话 `.mcp.json`**（与 agent loop 同一身份源）；无需签发端点 |
| Q7 | runs body 扩展（B2） | **做，时机 Phase 1.5**（两端各一处小改，§九-9.2 B2）；Phase 1 先用 provider 默认范围 |
| Q8 | 结构化上传 | **Phase 1 不做**（多文件循环顶住），Phase 2 做（§九 A4） |

## 八、评审记录（2026-09-22）

对本文的代码级评审结论：**方向成立，分层与先例选择正确；P1 两处需修正后才能进入 Phase 0，P2 三处需补充约束，P3 若干处修订表述。**

### P1-1 身份一致性必须升格为硬性验收标准（修订 §三"鉴权桥接"）

核实：本项目 agent loop 已有两种身份形态（`services/agent_loop/identity.py`——`header`=服务 key + `X-Intellect-User` 归因；`token`=用户自己的 member token 委托）；intellect-rag-app 侧 `AUTH_RAG_SERVICE` 路径从 `X-Intellect-User` 解析身份（`api/apps/__init__.py:287-328`），team/project 归属需要同时带 `X-Intellect-Team/Project` header 才会触发成员关系同步与 ownership 注入（`api/apps/__init__.py:660`）。

问题：知识代理与 agent loop 若不从**同一身份源**取凭据，`KnowledgebaseService.accessible()` 的 scope 就会错位——UI 里建的库，turn 时检索不到；或 A 用户能看到 B 用户的库。这是数据隔离问题，不只是实现选型。

修订：知识代理**必须**复用 `identity.py` 同一解析路径（同一 profile、同一 identity_store 条目、同一组 team/project header）。验收用例固定为两条：① UI 创建的 KB 对同一用户的 turn 可检索；② 对另一用户不可见。若运营者切换 agent-loop `identity_mode`，知识代理行为随之联动——写入部署文档。

### P1-2 会话级 KB 勾选是新增管线，不是"照抄 kag_block"（修订 §五、§六 Phase 1）

核实：`UnifiedContext` 无 `knowledge_bases` 字段（`core/context.py:61`）；`_build_request` 现有 blocks 全部来自全局设置（kag）或既有上下文字段。但 `source_manifest` 是"每 turn 勾选 → turn 输入 → context 字段 → prompt 块"的完整先例，sessions 表已有 `preferences_json`（`sqlite_store.py:179`）。

问题：完整勾选管线 = composer UI → **WS turn 契约加字段**（触发服务端模型 + 生成契约 + 测试三件套）→ UnifiedContext 字段 → manifest 远程拉取（turn 热路径上的新外部调用，需超时/缓存/降级）→ grounding 块 + 权限放行按勾选生成。原文按"照抄 kag_block"计入，低估了工作量与风险面。

修订：Phase 1 的 chat 接线降级为**设置项全局默认 KB**（与 kag_block 同构，零契约变更）；composer 勾选移到 Phase 1.5，复用 source_manifest 管线模式实现。

### P2-1 CLI 后端矩阵行仅对 Claude Code 成立（修订 §五）

核实：`ensure_session_mcp_config` 写的是 `.mcp.json` + `.claude/settings.json`——权限放行只有 Claude Code 读；KAG 设计文档明确 codex 侧（config.toml）未代码化。另 `.mcp.json` 中静态凭据落盘于每个会话工作区，Q6 的分量比原文更重；MCP server URL 必须与 CLI 进程同主机可达，需写入部署前提。

### P2-2 "intellect-team 原生挂 MCP"是未验证的外部假设（§五）

本次调研未覆盖 intellect-team 仓库。Phase 0 增加确认项：其 MCP/插件客户端的挂载方式与配置归属。Phase 1 的 KB 范围限定不依赖 Q7 协议扩展——`intellect_retrieval` 本身有 `dataset_ids` 参数，由 grounding 块在 prompt 中携带代理层解析好的 dataset_ids 即可实现勾选范围限定。

**已解决（2026-09-22 二轮调研，intellect-team 仓库已覆盖）**："Plugin 方式"确认为 RAGProvider 子系统（`plugins/rag/`，`kind: rag`），且已捆绑 `intellect-rag` provider 直连 rag-app——挂载是配置级工作，唯一代码缺口是 run 级 kb_ids 透传；"经 MCP server 挂载"对 intellect-team 不再必要（MCP 留给 CLI 与其它 HTTP 后端）。详见 §九-9.2。

### P2-3 Phase 1 范围偏大，建议切分（修订 §六）

原文 Phase 1 = merge-feasibility 阶段 3 + agent loop 接线 + composer 勾选。建议切成：**1a 管理闭环**（列表/创建/上传/进度/检索试玩，无 turn 路径改动）→ **1b chat 接线**（设置级默认 KB grounding + CLI `.mcp.json` + intellect-team 挂载确认）→ **1.5 composer 勾选**。与既有可行性文档的验收节奏对齐，先把最敏感的 turn 契约隔离在切分边界之外。

### P3 修订项

- **Q1 表述**（§七）：翻译成本去向要写明——"复用 REST"意味着翻译落在 UI 移植的 parse 函数里（`features/knowledge/api/*` 按 DeepMentor 响应形状编写）；两方案成本此消彼长，决策依据改为"愿意在哪个仓库维护翻译层"。Phase 0 的契约清单交付物不变。
- **Q3 修正**（§七）：不保留引擎表单壳（`CreateKbModal` 1864 行大头是表单），只保留 `engine` 字段 + 轻量目录注册（名称/图标/描述）；第二个引擎落地时再引表单分发。
- **§四映射表两处降级**："文件移动/建文件夹"移 Phase 2（虚拟文件树与 raw/ 目录语义不同）；"重索引"行注明 intellect 侧是按文档重解析 + 图谱任务，无 version-N 整库版本语义，UI 的索引版本 tab 砍掉。补充正面发现：`GET /datasets/<id>/ingestions/<log_id>` 已有日志端点，进度日志轮询可部分兑现。
- **部署面**（§六风险）：单引擎方案使 openkg-webui 部署隐含整套 intellect 基础设施（Redis/Task Executor/docStore/对象存储 + 9380 进程）；settings 未配置知识服务时，`PRIMARY_NAV` 入口必须隐藏（对齐 merge-feasibility 阶段 5 验收"服务可选，不暴露失效入口"）。
- **上传代理实现注记**：multipart 大文件经 FastAPI 代理需流式转发（现有 KAG 代理仅 JSON 先例）；Next 侧 `proxyClientMaxBodySize: 210MB` 已确认够用。
- **替代源记录**：intellect-webui 已有消费同一 `/api/v1` 的知识库管理页面，作为契约形状的活参照保留；UI 仍按用户指定以 DeepMentor 为源，理由（更完整的多引擎 UX 与产品形态）记录在案。

### 确认项（评审通过）

- 不复活已移除的 MCP client 栈：MCP 仅以"给外部 CLI 进程写配置文件 + 外部服务自挂客户端"的形态出现，与 AGENTS.md "no tool layer" 一致。
- 前端同源约束的处理正确（代理路由而非直连），子路径部署规则不受影响。
- "暂只支持 intellect-rag 功能足够"的结论在 P3 映射表修正后维持成立。
- KAG 先例的代码引用全部核实无误（`kag_grounding_block` :51、`ensure_session_mcp_config` :104、接线点 capability.py:716-758、`_normalize_kag`）。

## 九、增量功能清单（intellect-rag-app / intellect-team）

依据 2026-09-22 二轮定点调研（intellect-team 仓库首次覆盖；intellect-rag-app 定点补查）。两点发现修订了前文判断：

1. **intellect-team 的"Plugin 方式"不仅存在，而且已捆绑现成实现。** 其 RAGProvider 子系统（`plugins/rag/`，`kind: rag`，每次最多一个 active provider）里已有 `plugins/rag/intellect-rag/`：RAGHttpClient 直连 intellect-rag-app `/api/v1`（`INTELLECT_RAG_SERVICE_URL` + `INTELLECT_RAG_API_KEY`），每请求从 member context 注入 `X-Intellect-User/Team/Project`，对外暴露 `intellect_search(kb_ids)`、`intellect_list_kb` 等工具，并在每 turn 做 prefetch 检索注入与 system_prompt 块。**knowledge 引擎接入 intellect-team 因此主要是配置 + 一处小代码，不需要经 MCP server。**
2. **intellect-rag-app 的"缺口"比 §二.2.2 估计的更小。** 服务间鉴权只需 env（不要求 GATEWAY_MODE）；上传已支持多文件与 `parent_path` 存储前缀；ingestions 已含进度 + 日志文本。真正的代码新增收敛为 4 项（A3/A4/A6/A7，其中两项在 Phase 2 之后）。

### 9.1 intellect-rag-app 增量清单

| # | 功能 | Phase | 类型 | 落点 | 说明 |
| --- | --- | --- | --- | --- | --- |
| A1 | 服务间鉴权启用 | 1a | 配置 | env `INTELLECT_RAG_API_KEY` | 已核实：该 env 非空即启用 `AUTH_RAG_SERVICE`（`api/apps/__init__.py:285-290`），不要求 GATEWAY_MODE；`X-Intellect-User` 无匹配时幂等建户（`ensure_team_user`，`user_service.py:48-88`，自动补 tenant/membership）。安全注记：此路径直接信任 `X-Intellect-Role` header，openkg-webui 代理只转发会话派生身份 |
| A2 | OpenAPI 契约补齐 | 1a | 契约 | `api/openapi/spec.py` 追加 `spec.path()` 块（现成手写注册机制，现有 5 组路径照抄模式） | 范围按 Phase 0 契约清单定，预计覆盖：datasets CRUD、documents upload/list/preview、ingestions(+summary/log)、retrieval、files。消费者是 openkg-webui `contracts:generate` 管线与 AgentUI typegen |
| A3 | 默认知识库（per user） | 1b | 新增小端点 | 二选一：User 表加列复用 `PATCH /users/me` 透传（`user_api.py:302-373` 为黑名单式过滤，加列即通）或新建 per-user settings 表 | 已核实无现成 per-user 偏好存储（user 表仅 language/timezone 等；admin `/variables` 是全局 `SystemSettings`）。前者零新端点但有 schema 卫生问题，后者干净，需定夺 |
| A4 | zip / 文件夹结构化上传 | 2 | 新增 | document_api 上传链路 + 文件树节点 | 已核实：多文件（`files.getlist`）与 `parent_path` 存储前缀已有；缺 zip 解包（`.zip` 会被引擎侧拒收）与文件树节点自动创建。**Phase 1 用逐文件循环即可**（多文件端点已支持），Q8 结论改为"Phase 1 不做" |
| A5 | MCP server 部署配置 | 1b（仅当 CLI 后端在范围内） | 配置 | env `INTELLECT_MCP_HOST/PORT/LAUNCH_MODE/HOST_API_KEY` | 默认绑 127.0.0.1:9382 恰好适配 CLI 同机场景；host 模式 Bearer 透传已核实（`server.py:483-504,623-654`）；无 CORS 中间件——仅限同机或反代，不应对外网开放 |
| A6 | MCP 工具扩展（可选） | 2 | 新增 | `mcp/server/server.py` | `intellect_list_documents` / `intellect_list_datasets`，支撑"库里有什么文档"类问题（现仅 `intellect_retrieval`，server.py:514） |
| A7 | KnowledgeEngine provider 接口 | 3 | 新增 | facade blueprint | 第二引擎引入时再定义（§三引擎抽象），当前不做 |

**明确不需要做的**（原疑虑消除）：进度/日志端点——`GET /datasets/<id>/ingestions` 已含 `progress/progress_msg/operation_status/process_duration`，`/ingestions/<log_id>` 返回含 `dsl` 的完整记录，足以支撑 UI 轮询；批量上传端点——多文件已支持；MCP token 签发端点——见 §七 Q6 修订。

### 9.2 intellect-team 增量清单

| # | 功能 | Phase | 类型 | 落点 | 说明 |
| --- | --- | --- | --- | --- | --- |
| B1 | 启用/核对 intellect-rag RAGProvider | 1b | 配置 | `~/.intellect/config.yaml`：`rag.provider: intellect-rag` + env `INTELLECT_RAG_SERVICE_URL` / `INTELLECT_RAG_API_KEY`；确认 `platform_toolsets.api_server` 含 `rag` toolset（该平台默认 toolset 不含 rag，若实例显式配置过平台工具列表则需加入） | 插件现成：`intellect_search(kb_ids)` / `intellect_list_kb` / 上传 / 图谱工具（RBAC 与 member_rbac 双向对齐）+ 每 turn prefetch 注入 + system_prompt 块；RAGHttpClient 每请求注入 X-Intellect-* 身份——与 P1-1 身份一致性天然对齐（UI 代理与 team 检索走同一 member 身份派生）。RAG 接入是产品内建 opt-in，目标部署可能**已启用**——本项以核对为主，既有接入的影响分析见 §十一 |
| B2 | run 级 kb_ids/scope 透传 | 1.5 | 少量代码（两侧） | team 侧：api_server adapter `_handle_runs` 解析 body `kb_ids/scope` → agent runtime context → `conversation_loop.py:1037` 改为 `prefetch_all(..., kb_ids=..., scope=...)`（`rag_manager.py:172-215` 已支持透传，`_resolve_search_selection` 已支持显式参数优先）；openkg-webui 侧：`RunsAgentLoopBackend.run()` payload 加 `kb_ids/scope` | **严禁走 env 通道**：`INTELLECT_RAG_KB_IDS` 是进程级变量，api_server 进程并发 run 会互相串味。这是 Q7 的最终形态：两端各一处小改，非协议设计 |
| B3 | 部署模板与文档 | 1b | 文档 | deploy/docs（已有 rag-deployment-sync.md 可扩展） | 固化 B1 配置；写明 team 侧 `INTELLECT_RAG_API_KEY` 与 rag-app 侧 A1 是同一把 key；写明 api_server 需含 rag toolset |
| B4 | per-user MCP 鉴权 | 暂缓 | 较大新增 | `tools/mcp_tool.py`（header 模板/动态注册） | 仅当硬性要求 intellect-team 走 MCP 且每用户身份时才需要——B1 路径已按请求注入身份，此项不做 |

**集成注记（openkg-webui 侧对齐）**：
- runs 事件无 `tool.failed`：工具失败 = `tool.completed` + `error:true`（其 adapter 对 `_make_run_event_callback` 的约定），`RunsAgentLoopBackend` 事件映射需按此归一。
- intellect-team 已有 `/v1/rag/retrieval`、`/v1/rag/knowledge-bases*` 管理代理——openkg-webui 知识中心**不**经由它们（直连 rag-app），避免双重代理与两套身份路径。
- grounding 块职责收缩：provider 自带 prefetch + system_prompt_block，openkg-webui 对 intellect-team 不再注入 manifest 块（Phase 1b 靠 provider），manifest 块仅服务 CLI 后端。

### 9.3 修订后的接入拓扑

```
openkg-webui ── /api/knowledge 薄代理（服务 key + X-Intellect-*）──► intellect-rag-app REST :9380（知识中心后端）
     │                                                                    ▲
     │ runs: prompt + kb_ids(B2)                              B1: RAGProvider 插件直连 REST（同一把 key + 身份 header）
     ▼                                                                    │
intellect-team ═══════════════════════════════════════════════════════════╝
     （经自带 RAG provider 插件，不经 MCP）

CLI 后端（claude-code / codex / opencode）── session .mcp.json ──► intellect-rag-app MCP server :9382
其它 loop（hermes 本地 ACP / agentscope / custom）── 各自 MCP client ──► 同上
```

**推论**：若 Phase 1 的 agent loop 只有 intellect-team，`A5/A6`（MCP server）可整体延后——知识引擎接入 = rag-app 一个 env（A1）+ team 一份配置（B1）+ 两处小代码（B2 两侧）。

## 十、DeepMentor 许可确认（Phase 0 该项关闭）

**结论：Apache-2.0，可移植；与本项目同许可，无出入向冲突。义务仅剩署名。**

- DeepMentor `LICENSE` 为 Apache-2.0 全文，版权声明 `Copyright 2025 Data Intelligence Lab, The University of Hong Kong`；`pyproject.toml` `license = "Apache-2.0"`；仓库**无 NOTICE 文件**。
- 本项目 `LICENSE` 同为 Apache-2.0——同许可之间复制/修改/再分发均无障碍，且 Apache-2.0 附带专利授权。merge-feasibility §5.4 担心的"未找到 LICENSE"情形不成立（该文当时审的是 agentui，不是 DeepMentor）。
- 拟移植的知识中心代码目录（`deepmentor/knowledge/`、`deepmentor/services/rag/`、`web/components/knowledge/`、`web/features/knowledge/` 等）**无任何第三方许可头**；根目录 `THIRD_PARTY_NOTICES.md` 只覆盖 Codex OAuth（CSSwitch，MIT）与飞书/企微协议（hermes-agent，MIT）的"概念借鉴、独立实现"，与知识中心无关，无需随迁。
- **随首批移植代码落实的义务**（Apache-2.0 §4）：
  1. 保留许可副本——本仓库已含 Apache-2.0 LICENSE，无需动作；
  2. 署名——在仓库第三方声明处（建议新增 `THIRD_PARTY_NOTICES.md` 或 README 致谢段）记录：*Portions of the knowledge center UI/backend are derived from DeepMentor (Apache-2.0), Copyright 2025 Data Intelligence Lab, The University of Hong Kong*；
  3. 标注修改——移植即修改，首批移植的文件头部注明 `Derived from DeepMentor <path>, modified`；
  4. 若未来引入带 NOTICE 的上游组件，随迁其声明。

## 十一、既有 Plugin 接入（intellect-rag → intellect-team）的影响分析

前提（已核实）：intellect-team 的 RAG 接入是**产品内建、opt-in**——`.env.example` 明示"RAG is opt-in … The agent loop and Rust gateway construct the HTTP RAG provider only when RAG_SERVICE_URL is set"；且为**双路径**：Python api_server 插件与 **Rust 网关**（`config.rs` 读同一 `INTELLECT_RAG_API_KEY`），一行 `RAG_SERVICE_URL` 可同时配置两侧。目标部署已启用（当前集成状态）。对方案的影响：

1. **知识中心是给既有后端关系加管理面，不是新建数据域。** 存量 dataset 已被 team 侧流程创建与消费（`intellect_create_kb` / `intellect_upload` 等工具）。KC 上线即见存量数据（零迁移，收益）；但 KC 必须容忍"非 KC 创建"的 dataset（无 KC 元数据、任意 parser_config），且**删除/重命名 dataset 会破坏正在使用它的 team 流程**——尤其被 `INTELLECT_RAG_KB_IDS` 或静态 config 固定引用的库。openkg-webui 看不到 team 侧引用，无法自动检测：KC 破坏性操作加确认文案，部署文档说明风险。
2. **A1 细化：必须复用同一把服务 key。** team 的 Python 插件与 Rust 网关共享 `INTELLECT_RAG_API_KEY`（env 文档明示）；openkg-webui 若另发一把 key，rag-app 侧会出现两个服务身份、两套自动建户路径。A1 从"配置一个 env"细化为"**复用 team 既有 key** + `X-Intellect-User` 归因"。
3. **B1 细化：从"启用"变为"核对已启用配置"**（B1 行已改）。B2 的向后兼容有既有机制背书：`_resolve_search_selection` 优先级为 显式参数 > 会话 env > 静态 config——B2 只新增"显式参数"入口，**参数缺席 = 现状行为**，既有 `/v1/runs` 调用方不受影响；以此作为 B2 的回归门禁。
4. **默认范围 grounding 的行为交叠（新决策点）。** provider 启用后，未带 kb_ids 的 turn 也会按静态 config/env 的默认范围 prefetch 并注入召回块——openkg-webui 用户未勾选 KB 的 turn 将被动获得 team 级默认库的 grounding。两个选项：①接受（默认库兜底，符合团队部署预期）；②B2 同时定义"显式空"语义（openkg-webui 恒发 `kb_ids/scope`，未勾选时发禁用值）——需确认 `_resolve_search_selection` 对显式空的关闭语义（现代码未见处理，需一处小改）。倾向 ②，留给讨论。
5. **所有权可见性链（P1-1 的产品化）。** openkg-webui 代理创建 dataset 的 ownership 由携带的 `X-Intellect-User/Team/Project` header 决定；**不带 Team/Project header 时落 private，intellect-team 的成员 agent 将检索不到**。因此 KC 建 KB 应让用户选可见范围（个人/团队），代理据此决定携带哪些 header——P1-1 从"验收标准"升级为产品行为设计。
6. **双路径确认（Phase 0 增补）。** openkg-webui 的 runs 指向 api_server（Python，:9091），但 Rust 网关亦有检索路径——需确认目标部署中 openkg-webui 的 runs 实际由哪个进程服务；若存在经 Rust 网关的 runs 形态，B2 的 kb_ids 透传需在其对应实现同步（当前清单仅计 Python 侧）。

## 十二、TODO：KAG 纳入知识中心统一管理（另开计划）

背景：KAG（OpenSPG 语义图谱推理）与 Intellect-RAG（向量/图谱检索）同属"知识源"类应用，目前在本项目分开管理——KAG 走 `/kag` 页面组 + `/api/kag` 代理 + `kag` settings 块，知识中心将走 `/knowledge` + `/api/knowledge` + `knowledge` settings 块。

**TODO（知识中心 Phase 1/2 落地后另行开计划实施，不进当前 phases）**：

- 将 KAG 作为知识中心的第二个"知识引擎"条目纳入统一管理：KC 引擎目录（§三引擎抽象的 UI 目录壳）加 `kag` 条目，统一导航入口与列表/详情交互。
- 统一设置面：评估合并 `/settings/kag` 与 `/settings/knowledge`（决策 Q5：知识中心已接管该路由并并入文档解析设置）为一个"知识源"类目下的两个引擎配置页。
- 管理面归并：`/api/kag` 的管理类端点纳入 `/api/knowledge` 代理体系（或作为同层兄弟前缀保留，在新计划中定）。
- **不动**的部分：KAG 的推理执行面（bridge/solve、`kag_grounding_block`、`.mcp.json` 注入、`ensure_session_mcp_config`）与知识库管理是两类关注点，保持原样，仅统一"知识源管理"层。
- 数据架构差异注意：KAG 数据在 OpenSPG，不在 intellect-rag-app——KAG 条目预计走 openkg-webui 侧目录（路由到既有 `/api/kag` 代理），而非 rag-app facade 的 provider；此取舍在新计划中定，验收标准为"KC 引擎目录加第二个条目不需要改动知识中心核心"。

## 十三、TODO：团队/项目可见范围打通（本期不实现，2026-09-25 裁决）

背景：知识中心建库的可见范围由上游 `visibility` 列决定（`private | tenant | team | project`），
但 **team/project 目前不可产生**——KC 代理不携带 `X-Intellect-Team/Project`（link 记录里
这两字段恒为空，网关 `/api/members/me` 不回传），且上游对 `imt_` 令牌拒绝据头建成员关系。
`team_membership`/`project_membership` 两表当前 0 行，故 `can_access_resource` 永不命中
team/project 分支。

**裁决（2026-09-25）：本期暂不实现**，reason：跨 intellect-team / intellect-rag-app /
openkg-webui 三仓库，首要前置（网关返回成员团队归属）不在本仓库内。

**TODO（另行立项时实施）**：

1. **身份源**（intellect-team）：`GET /api/members/me` 返回 member 的 team/project
   （`intellect-gateway/src/platform/members_api.rs` 的 `MeResp`），或由 KC 侧提供显式
   选择并落盘到 link 记录；
2. **成员关系**（intellect-rag-app / intellect-rag）：`team_membership`/`project_membership`
   真实落行（当前为空表）；
3. **上游策略**（intellect-rag）：允许 `imt_` 调用方声明 team/project 并**校验归属**
   （当前 `sync_membership.py` 对 `imt_` 一律拒绝——拒绝得对，但使该能力不可用）；
   同时 ownership 注入侧对 team/project 做 clamp/查表——现状「`create` 无 clamp、
   `sync_membership` 拒绝」会产出 *team_id 可伪造且无人可见的孤儿库*；
4. **KC 侧**：无需改动。状态端点 `create_visibility` 已按「实际会发的归因头」
   推导（返回 `private`|`team`|`project` **范围字符串**），三环补齐后自动变为
   team/project，创建对话框与徽标随之如实反映。
   （2026-09-25 评审修正：原设计为布尔"能否共享"。改为范围字符串，因为上游
   **两个方向都不可选**——有头时 `permission=me` 也无效，UI 必须陈述而非让用户选。）

**验收标准**：KC 建"团队"库 → 上游 `visibility=team, team_id=<id>`；同团队另一成员可见、
非成员不可见；聊天召回可见集与列表一致。

**本期待做**：无（本期已用「不承诺」兜底：`create_visibility` 如实陈述范围，
非法 `permission` 拒绝 400，不产生 legacy 说 team、visibility 落 private 的错配数据）。

依据：`docs/plans/knowledge-center-visibility-scope-fix.md` §二-B、§三-P2、§六（实施记录）。
