# 知识中心 UI 对齐方案：详情页与 DeepMentor 差距清单与完善计划

- 日期：2026-09-26
- 状态：**评审完成（2026-09-26，记录见 §八）**——方案已按评审修订；P0 带前置任务 T0（预览链路鉴权验证）
- 依据：DeepMentor（`~/projects/DeepMentor`，同源 fork）知识库 UI 代码级对比；上游 rag-app
  OpenAPI 实测（`http://127.0.0.1:9380/openapi.json`）；本仓 `api/routers/knowledge.py` /
  `services/knowledge/engines/` / `web/features/knowledge/` 现状核对
- 前置：Phase 1a/1b/1.5/2/3 已交付。当前知识中心 = Intellect RAG 薄代理
  （`engines/intellect_rag.py` provider + `routers/knowledge.py` 门控与映射 + `web/features/knowledge/` 四层前端）

## 一、根因与定位

| | openkg-webui 知识中心 | DeepMentor 知识库 |
| --- | --- | --- |
| 定位 | Intellect RAG（:9380）的**薄代理 UI**——"管道通了、窗口没开" | **完整知识库产品**（9 引擎、7 种连接形态） |
| 详情页 | 单列长页纵向堆叠（`KnowledgeDetailPage.tsx`，579 行） | 8 个标签页的主从式工作台（`KnowledgeBaseDetail.tsx`） |
| 血缘 | 部分代码移植自 DeepMentor（代码注释 `Derived from DeepMentor Apache-2.0`），只移植了上传→解析→检索最小闭环 | 持续产品化，长出了预览/文件树/版本/引擎管理 |

**关键事实**：两边对接**同一个上游 rag-app**。实测其 OpenAPI，能力远超本仓代理目前暴露的
范围——chunk 全 CRUD、知识图谱（`/knowledge_graph`、`/run_graphrag`）、索引构建
（`/index?type=graph|raptor`）、文档预览全部现成。**本方案主要是"给已有管道开窗"，不改上游。**

## 二、差异清单

### 2.1 页面架构与导航

| 能力 | openkg-webui 现状 | DeepMentor 现状 |
| --- | --- | --- |
| 详情页结构 | 单列长滚：文档表 → 日志 → 检索试玩 → GitHub 面板 → Web 面板 | 标签页 `files/add/github/web/versions/devices/intellect/settings`，按库类型动态裁剪（`knowledge-helpers.ts:296`） |
| 深链 | 仅 `/knowledge-center/[datasetId]` | KB 名进 path + `?section=`、`?file=` 直达面板/预览 |
| Files 布局 | 平铺表格全宽 | 主从：左文件树（220px 可折叠）+ 右预览（`KbFilesTab.tsx:65`） |

### 2.2 文档管理（差距最大）

| 能力 | openkg-webui | DeepMentor |
| --- | --- | --- |
| 列表形态 | 平铺表格 Name/Status/Chunks/Size/操作 | 树形文件浏览器（按 `raw/` POSIX 路径建树、骨架屏、`KbDocumentList.tsx`） |
| 分页/搜索/筛选 | ❌ 前端写死 `page_size=100`（api.ts:104）——**代理已支持 `page/page_size`（knowledge.py:321-332），纯前端缺口** | 树形+计数徽章组织（亦无表格分页） |
| 文件夹操作 | ❌（上传保留目录语义，展示平铺） | ✅ 新建文件夹、拖拽移动、两步删除 |
| **文档预览** | ❌ **代理端点已存在**（`GET /documents/{doc_id}/preview` knowledge.py:1016、`/thumbnails` :1023）**前端 0 引用** | ✅ 9 类渲染器懒加载 + 全屏 + 下载 + 抽取文本回退（`KbFilePreview.tsx`） |
| 文档详情 | ❌ 点击文档名无动作；单文档端点已有未接（knowledge.py:963） | 点击即预览（主从联动） |
| 上传体验 | 按钮多选+文件夹+zip 解包；**无拖放区/进度条/预校验** | 拖放区（拖入实时校验、逐文件错误、去重合并、摘要卡）、目标文件夹选择（`FileDropZone.tsx`） |
| 解析进度 | 5 态徽标+百分比、SSE 日志流+4s 轮询 ✅（两边对齐） | SSE+WS 双通道、终端日志面板、**本地持久化任务历史**（`KbUpdateHistory.tsx`） |
| 失败恢复 | 单个 Reparse、批量 Stop | 专用 Retry、`error_code` 分类→模型设置深链、错误态仍可替换上传 |

### 2.3 知识库级能力

| 能力 | openkg-webui | DeepMentor |
| --- | --- | --- |
| 设置 tab | ❌ 创建后不可改名/描述 | ✅ 元数据、**默认知识库**、危险区删除 |
| 默认知识库 | ❌ 代理 `/preferences` 端点已有未接（knowledge.py:217/226） | ✅ |
| 索引版本 | ❌ | ✅ embedding 签名多版本（Active/Stale/Legacy、mismatch 检测） |
| 检索试玩 | ✅ **openkg 独有**，但参数**前端硬编码**（api.ts:182-184 threshold=0.2/size=10）——**代理与引擎均已支持 top_k/threshold/vector_similarity_weight/page/size（knowledge.py:997-1013、intellect_rag.py:414-436），纯前端缺口** | ❌ 不存在（openkg 反超点，未做完） |
| chunk 管理 | ❌ | ❌（**双方都缺；上游 API 支持 chunks 全 CRUD**，先做先领先） |
| 知识图谱 | ❌（KAG 域已有 cytoscape+fcose 画布 `GraphExplorerSection.tsx` 可复用） | ✅ graph/RAPTOR 构建+进度（`KbIntellectRagSection.tsx`），**无可视化画布** |
| 多引擎 | 引擎抽象已落地（`engines/registry.py`），**UI 无感知** | ✅ 引擎网格 + EngineDetail 完整配置页 |

### 2.4 聊天集成

| 能力 | openkg-webui | DeepMentor |
| --- | --- | --- |
| 会话级知识库勾选 | ✅ `KnowledgeSessionPicker`（会话偏好持久化） | ✅ `KnowledgeSelector`（基本对齐） |
| **引用标注** | ❌ 未见 `[rag-N]` 类引用渲染 | ✅ 上标 citation-group + 锚点跳转（`RichMarkdownRenderer.tsx:535-564`） |

### 2.5 工程质量

两边同为 Next.js + Tailwind + CSS 变量 + font-serif 标题 + 全量深色模式，设计语言同源，
视觉差距在**信息密度与功能面**而非审美。openkg 的分层纪律（transport/纯模型/组件 +
depcruise 守护）更好；DeepMentor 的后端进度 `message_key` 模板化本地化（openkg 未做）。
i18n：本仓知识中心 70 key 中文零缺失，新增 UI 需同步补 `web/locales/zh/app.json`。

## 三、方案总原则

1. **复用 DeepMentor 已验证的 UI 模式 + 本仓已有分层纪律**；全部基于上游已实测存在的 API。
2. **不做违背本仓定位的移植**：不搬 7 种连接形态与 9 引擎全量（与租户/可见范围模型冲突）；
   不搬"检索参数引擎级全局配置"（坚持 per-库 + 会话级，现有 `KnowledgeSessionPicker` 路线更合理）。
3. **遵守 AGENTS.md 子路径部署规则**：所有新资源 URL 走 `apiUrl()`；预览二进制与缩略图
   不得裸拼路径；SSE 沿用 `logsStreamUrl()`。
4. **契约管线联动**：新增/修改代理端点必须四层同步——engine 方法 → router（`response_model`
   实体化，遵守 T3 信封归一）→ `contracts/export.py` 重导出 → `web/contracts/generated/api.ts`
   重生成；`test_frontend_contract_export` 与 `contracts:check` 把关。
5. 所有 chunk/preview 类新端点先过 §八-R2 的鉴权验证再放开。

## 四、分阶段计划

### P0 —— 详情页骨架 + 文档预览（约 5~6 人日）

| # | 任务 | 落点 | 依赖 |
| --- | --- | --- | --- |
| T0 | **前置：预览/缩略图链路鉴权实测**（§八-R2） | 脚本探针（沿 `scripts/kag_a0` 惯例） | 上游实测 |
| T1 | 详情页重构为标签页 `Documents / Sources / Retrieval / Settings`（动态裁剪 + 下划线 tab，参照 `kbDetailSections`；复用 `KnowledgePageFrame`）；`?section=` 深链 | `web/features/knowledge/components/` | — |
| T2 | 文档详情抽屉：接通 preview/thumbnails 代理端点；懒加载渲染器先做 PDF（pdfjs-dist 已在）/图片/文本/Markdown（react-markdown 已在）四类 + 全屏 + 下载；**T0 通过后放开** | 前端抽屉 + `model.ts` 扩展 | T0、T1 |
| T3 | 上传区升级：拖放区 + 逐文件预校验摘要卡（参照 `FileDropZone.tsx` 深度计数防抖与实时校验）；保留 zip/文件夹逻辑 | `KnowledgeDetailPage.tsx` | T1 |
| T4 | 默认知识库：接通既有 `/preferences` GET/PUT + 设置入口 | api.ts + Settings tab | T1 |

### P1 —— 列表与任务体验（约 5~6 人日）

| # | 任务 | 落点 | 依赖 |
| --- | --- | --- | --- |
| T5 | 文档列表增强：状态筛选、名称搜索、**分页接通**（代理已支持）、按 `location` 目录分组展示；T2 抽屉升级为主从面板（参照 `KbFilesTab`） | `DocumentTable` | T2 |
| T6 | 任务中心：解析历史（`GET /ingestions` 已有）+ 终端样式日志面板 + 进度条 + 失败分类与批量重试；对齐"错误态仍可替换上传" | 详情页 Documents tab | T1 |
| T7 | 检索试玩参数开放（**纯前端**）：threshold/top_k/`vector_similarity_weight` UI 可调、chunk 完整内容展开；`api.ts` 去硬编码 | `RetrievalPlayground` | T1 |
| T8 | Settings tab（第一批）：改名/描述（engine 补 `update_dataset`，上游 `PUT /datasets/{id}` 已被 DeepMentor `client.py:273` 验证）；embedding 信息展示（见 §八-R4 谨慎项） | engine + router + 契约 + 前端 | T1 |

### P2 —— chunk 管理与知识图谱（约 6~8 人日）

| # | 任务 | 落点 | 依赖 |
| --- | --- | --- | --- |
| T9 | chunk 浏览/编辑：engine 补 `list_chunks/add_chunk/update_chunk/delete_chunk`（上游 `GET,POST/PATCH,PUT,DELETE …/chunks[/{chunk_id}]` 全套实测存在）；首版范围见 D4；UI = 文档详情内 chunk 列表（T3 信封归一口径进 `models.py`） | 四层全链 | T0、T2 |
| T10 | 知识图谱：构建入口（`POST /datasets/{id}/index?type=graph\|raptor`，D5）+ 状态轮询（`GET …/index?type=`）+ 取数 `GET /knowledge_graph`（先 probe 载荷）+ cytoscape/fcose 画布渲染（复用 KAG 域）；容量上限见 §七 | 四层全链 | T1 |
| T11 | 聊天引用标注 `[rag-N]`（**调研项**，D7）：先验证 agent-loop 网关是否透传引用标记，可行再移植 `RichMarkdownRenderer` 的渲染 | `web/features/chat/` | 调研结论 |

### P3 —— 长线（另立项）

- T12 多引擎 UI：移植 EngineDetail 模式（引擎网格 + 配置页），让 `registry.py` 被用户看见。
- T13 索引版本管理：依赖上游 `/embedding/check` 签名语义调研。
- T14 进度消息 `message_key` 模板化本地化。

### 明确不做

- DeepMentor 的 7 种连接形态（Obsidian/MarginNote/WeKnora/IMA/…）与 9 引擎全量移植。
- 引擎级全局检索配置面（坚持 per-库 + 会话级）。
- WS 双通道进度（SSE + 轮询已满足，见 §八-R6）。

## 五、决策记录

| # | 决策 | 结论与理由 |
| --- | --- | --- |
| D1 | 详情页形态 | **标签页**（对齐 DeepMentor），放弃继续加长单页；`?section=` 深链同步做 |
| D2 | 预览形态 | P0 用**抽屉**（改动小、表格不动）；P1 T5 引入树形主从后升级为**主从面板** |
| D3 | 新端点鉴权 | 一律走 `_engine_call` + dataset 作用域；preview/thumbnails/chunks 先过 T0 实测，必要时代理侧补 doc→dataset→visibility 校验 |
| D4 | chunk 编辑范围 | 首版 = 内容/关键词更新 + 删除 + 启停；不做新建（上游 POST 支持但首版不放开，视需求追加） |
| D5 | 图谱端点选择 | 构建走 `POST /index?type=graph\|raptor`（DeepMentor 已验证路径），**不用** `/run_graphrag`（语义未验证）；取数 `GET /knowledge_graph`，与 `/graph` 的差异由 T10 探针定 |
| D6 | 进度通道 | 维持 SSE + 轮询，不引入 WS |
| D7 | 引用标注 | 先调研后实施：网关是否透传 `[rag-N]` 决定 T11 去留 |
| D8 | embedding 信息 | **不得**把 `POST /datasets/{id}/embedding` 当查询用（可能是重建触发器，见 §八-R4）；信息展示以 T10 探针确认安全读取路径为准 |

## 六、风险与缓解

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| preview/thumbnails/chunks 上游 per-user 强制未验证，私有库可能被同租户用户经这些端点读到 | **高** | T0 实测（§八-R2）；不通过则代理侧补 doc→dataset→visibility 校验后再放开 |
| `/knowledge_graph` 载荷过大拖垮 cytoscape | 中 | 节点/边上限 + 采样；超限降级为"图谱构建中/过大"提示；只在用户显式进入时拉取 |
| 契约四层联动遗漏导致 `contracts:check`/`test_frontend_contract_export` 失败 | 中 | 每个任务的自查清单含"重导出 + 重生成 + 两道门过"；CI 已有门 |
| 新增字符串漏翻 | 低 | i18n 审计脚本只认 `t("…")` 字面量，编码时即写死字面量并同步补 zh |
| pdfjs 体积影响 `route_budgets` | 低 | 预览渲染器全部 `next/dynamic` 懒加载（DeepMentor 同款做法） |

## 七、验收口径

- 每个 P 任务：后端 pytest（含 `tests/api/test_knowledge_router.py` 增补）+ 前端
  `knowledge-parse.spec.ts` 增补 + `contracts:check` + `typecheck` 全绿；
- P0 完成标准：详情页四 tab + 文档抽屉预览四类格式 + 拖放上传，live 验证一条上传→解析→预览链路；
- P2 完成标准：同一库内完成"建 chunk 改删 → 图谱构建 → 画布浏览"闭环。

## 八、技术评审记录（2026-09-26）

评审方式：对照本仓代码逐条核实方案断言 + 实测上游 OpenAPI + 对照 DeepMentor 客户端实现。
以下发现**已据以修订 §四/§五**，修订处以（评审 R-x）标注。

### R1 后端已就绪项比原方案假设的多，两处工作量下调

- 检索参数：router **已**从 body 透传 `top_k/similarity_threshold/vector_similarity_weight/page/size`
  （knowledge.py:997-1013），engine 签名齐备（intellect_rag.py:414-436）。T7 降为**纯前端**任务（0.5 人日）。
- 分页：代理**已**支持 `page/page_size`（knowledge.py:321-332）。T5 的分页部分为纯前端。
- 默认知识库：`/preferences` GET/PUT 已存在（knowledge.py:217/226）。T4 无后端改动。
- 结论：P1 从"后端+前端"修正为"以前端为主"，总工作量相应下调。

### R2 预览/缩略图端点存在鉴权面缺口（新增 P0 前置 T0）

`GET /documents/{doc_id}/preview`、`GET /thumbnails` 以**裸 doc_id** 为作用域，代理侧仅有
`_require_enabled()`（knowledge.py:1016-1028），无 dataset 归属校验、无可见性校验。当前
安全性完全取决于上游按 `X-Intellect-User/Team/Project` 身份委托头做的数据级强制（search 已
返回 `denied_dataset_ids` 证明上游有此能力，但这**不是** preview 路径已强制的证据）。若上游仅
做到租户级，则**同租户其他用户可经预览端点读到私有库文档**——与 8e1e6ad 可见范围收口矛盾。
处置：新增 T0 实测（双用户/私有库/deny 断言）；不通过则在代理侧补 doc→dataset→visibility
校验后才放开 T2/T9。T9 chunk 端点同受此约束。

### R3 契约管线成本必须计入每个任务

本仓契约是生成物且有双门（`test_frontend_contract_export` + `web contracts:check`）。
凡动 router 签名的任务（T8/T9/T10）都隐含"engine → router → export.py 重导出 → api.ts
重生成"四步，漏一步即 CI 红。已写入 §三-4；工作量估算已含。

### R4 图谱与 embedding 端点存在语义歧义，先探针后接线

- 上游同时存在 `POST /datasets/{id}/index`（DeepMentor 用 `?type=graph|raptor`，已验证）、
  `POST /run_graphrag`、`GET /trace_graphrag` 三条构建/追踪路径，语义重叠未验证 → D5 选已
  验证路径，T10 探针确认 `/graph` vs `/knowledge_graph` 载荷差异。
- `POST /datasets/{id}/embedding` 与 `POST …/embedding/check` **都是 POST**——很可能是重建/
  校验触发器而非查询接口，**禁止当信息读取用**（D8）。T8 的 embedding 展示改由探针确认安全
  读取路径（若仅能经 dataset 详情字段获得，则以该字段为准）。

### R5 预览依赖零新增

`pdfjs-dist@6.2.108`、`react-markdown@10.1.0` 两边**同版本**已在位；cytoscape+fcose 已在
（KAG 域）。P0/P2 无新增重依赖，仅注意 pdfjs 走 `next/dynamic`。

### R6 不移植 WS 双通道（D6 依据）

DeepMentor 的 WS+SSE 双通道为其本地多引擎任务体系服务；本仓知识任务是上游 rag-app 托管、
SSE（`/logs/stream`）+ 4s 轮询已覆盖，引入 WS 只增加断线重连与 noop 订阅两类复杂度
（DeepMentor 自身为防悬挂 socket 做了大量补丁）。

### R7 引用标注可行性存疑（D7 依据）

`[rag-N]` 标记由上游 RAG 生成、经 agent-loop 网关回流。本仓 chat 链路是 agent-loop 后端
（CLI/HTTP）而非直连 rag-app chat，标记是否透传到前端正文未验证。T11 降为调研项，不进承诺范围。

### R8 工作量校准

原口径 P0=3~5 低估了 T1 标签页重构（动页面骨架+深链）与 T2 契约联动；校准后
P0≈5~6、P1≈5~6、P2≈6~8 人日。P2 的 chunk/图谱均含探针与 T0 复验。

### 评审结论

**通过（有条件）**：按本修订版实施；T0 必须先于 T2/T9 完成并留实测记录（沿
`scripts/kag_a0/results` 惯例归档探针输出）。D1-D8 与"明确不做"清单为后续实施的范围边界。
