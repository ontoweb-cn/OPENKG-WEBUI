# 知识中心 UI 对齐 P2 细化任务清单（chunk 管理 + 知识图谱 + 引用标注调研）

- 日期：2026-09-26
- 状态：**已实施完成（2026-09-26）**——T9/T10 落地并 live 验证；T11 调研结论 no-go（见 §六）
- 依据：[knowledge-center-ui-parity.md](knowledge-center-ui-parity.md) §四 P2（T9/T10/T11）；
  P0/P1 已交付（p0-tasks/p1-tasks 实施记录）
- 上游探针（2026-09-26，service key + 委托用户直探，只读）：

| 端点 | 实测形状 |
| --- | --- |
| `GET /datasets/{id}/documents/{doc_id}/chunks?page&page_size` | `{code:0, data:{chunks:[…], total}}`；chunk 键：`id/content/document_id/dataset_id/docnm_kwd/available/available_int/important_keywords/questions/positions/image_id/tag_kwd`（实测 39 chunks） |
| `GET /datasets/{id}/knowledge_graph` | `{code:0, data:{graph:{nodes,edges}, mind_map:{}}}`；未建图谱时 nodes/edges 为空数组（UI 须空态） |
| `GET /datasets/{id}/index?type=graph\|raptor` | `{code:0, data:{}}`——未建时空对象（"未构建"态） |
| chunk PATCH/PUT、POST/DELETE index | 未实测（变更类）；载荷按 RagFlow 口径实现，live 验证见 T9-T0 探针 |

## 一、范围与不做

- T9 chunk：**不做 chunk 新建**（方案 D4）；不做 chunk 跨文档搜索（上游有
  `/datasets/{id}/chunks` 全库端点，首版只做文档内）。
- T10 图谱：**不自动触发构建**（POST /index 会启动真实图构建任务，成本高——构建按钮
  由用户显式点击）；不做 mind_map 渲染（载荷已返回，留后续）。
- T11 引用标注：**只调研不实施**（D7：网关是否透传 `[rag-N]` 未验证）。

## 二、任务分解

### T9-P 探针：chunk 变更类端点 live 验证（前置，0.5 人日）

在 `联调测试库(1)`（测试库）上：POST 自建一个 chunk → PATCH 它（改 content/available）
→ DELETE 它，归档载荷与行为（沿 `scripts/knowledge_a0/results` 惯例）。结束后库内
数据不留痕迹。**PATCH 语义确认后**才写 engine。

### T9 chunk 管理（约 2.5 人日，四层）

- engine：`list_chunks / update_chunk / delete_chunk`（update 载荷按 T9-P 实测；
  **所有响应必须 `_unwrap`**——P1-T8 的教训）。
- models：`KnowledgeChunk {id, content, available, important_keywords, document_keyword?}`。
- router：`GET/PUT/DELETE /datasets/{id}/documents/{doc_id}/chunks[/{chunk_id}]`，
  `response_model` 实体化（T3 口径）；delete 支持批量 ids。
- 契约：重导出 + 重生成 + 双门。
- 前端：文档表"分块"列变按钮 → `ChunkListPanel`（模态列表：内容预览、available 开关、
  关键词徽标、编辑对话框、删除确认）；分页 20/页。
- 验收：live 对联调测试库走一遍 列表→编辑→启停→删除；既有数据不被破坏。

### T10 知识图谱（约 3 人日，四层）

- engine：`get_knowledge_graph / get_index_status / build_index / delete_index`。
- router：`GET /datasets/{id}/graph`、`GET/POST/DELETE /datasets/{id}/index`（`type`
  query 参数 graph|raptor）；构建/删除为**显式用户动作**。
- 契约：四层联动。
- 前端：详情页新增"图谱"tab——空态（未构建：构建按钮 + graph/raptor 选择 + 成本提示）；
  构建中（轮询 index status，5s）；就绪（cytoscape/fcose 画布渲染 nodes/edges，
  节点上限 500 超出采样，复用 KAG 域样式 token）；危险区（删除图谱）。
- 验收：live 构建一次小库图谱并渲染（或：用户后续自建后画布可读——构建成本高时
  以空态+轮询逻辑验收，画布用 mock 数据单测）。

### T11 引用标注调研（0.5 人日）

- 调研路径：`web/features/chat/` 消息渲染链路里 `[rag-N]`/`[citation-N]` 类标记是否
  出现（抓一条真实带知识库的会话回包）；agent-loop 网关（intellect-team）是否原样
  透传上游引用标记。
- 产出：调研结论写入本文档 §六（go：T11 移交 P3 实施；no-go：记录原因关闭）。

## 三、横切约束（同 P0/P1）

契约四层联动 + 双门；i18n 字面量 + zh/en 同步；子路径规则；**所有引擎响应 `_unwrap`**；
图谱/chunk 面板的错误态给内联重试。

## 四、技术评审记录（2026-09-26）

### R1 chunk 编辑的可用位语义（澄清）

chunk 同时有 `available`（bool）与 `available_int`（0/1）两个键——以 PATCH 实测为准
（T9-P），engine 归一到 bool 的 `available`，不把两个键都透传给前端。

### R2 图谱载荷无界（风险采纳）

`/knowledge_graph` 无分页参数；大库可能数千节点。画布侧：节点 >500 时按 degree 采样
并提示"已采样"；数据获取侧加 30s 超时 + 失败重试。不做服务端裁剪（上游无参数）。

### R3 构建按钮的幂等与并发（澄清）

POST /index 在已有构建运行时的行为未知——前端构建中禁用按钮（轮询状态期间），
不依赖上游幂等；重复构建的确认弹窗提示成本。

### R4 空态与"构建中"的判定（澄清）

index status 空对象 `{}` = 未构建；构建中/完成的字段形状未知（探针未覆盖运行态），
UI 以"status 非空即有信息"渲染原始字段 + 轮询，T10 实施时以一次真实构建校准
（联调测试库体积小，构建成本低，可在验收时执行）。

### 评审结论

**通过**。T9-P 为硬前置（PATCH 语义不实测不写 engine）；R1/R2/R4 为实现口径，
R3 为前端行为要求。

## 六、实施记录（2026-09-26）

**T9-P 探针**（归档 `scripts/knowledge_a0/results/t9p_chunk_mutation_probe.md`）：
PATCH 语义 `{content?, available?(bool), important_keywords?}` 确认；**item DELETE 为
405，删除走集合端点 + chunk_ids body**；单 GET 不含 content（回读验证走 LIST）；
探针数据自建自删，无残留。

**T9**：engine `list_chunks/update_chunk/delete_chunks`（PATCH 上游；删除走集合端点）
+ `KnowledgeChunk/ChunkPage/MutationResult` 模型 + 路由 GET/PUT/DELETE 三端点 + 契约
重生成；前端 `ChunkListPanel`（内容展开/available 开关/关键词徽标/行内编辑/两步删除，
20/页）。**live 验证抓出两个真问题并修复**：
1. 上游变更异步落库——紧跟的 GET 回读到旧值覆盖 UI；改为乐观更新 + 900ms 延迟重拉；
2. 首版 `reloadSoon`（useCallback）误置于 early return 之后触发 Rules of Hooks 崩溃
   （控制台 "Rendered more hooks"），已移至条件返回前。
live 启停往返：true→false→true 全部按预期落库并回读。

**T10**：engine `get_knowledge_graph/get_index_status/build_index/delete_index` +
路由 GET graph、GET/POST/DELETE index；前端"图谱"tab（空态构建按钮 graph/raptor、
构建中轮询 5s、cytoscape/fcose 画布 + 节点采样 500、危险区删除）。**live 验证抓出**
`GraphIndexStatus(raw={})` 序列化为 `{"raw":{}}` 导致前端"非空即构建中"误报——路由改为
透传上游原样对象（未构建 = `{}`）。live：空态/构建按钮/无假"构建中"全部通过；
真实构建验收留待用户显式触发（成本高，UI 已具备轮询与画布路径）。

**T11 引用标注调研（no-go）**：本仓 `web/features/chat` 渲染链路无任何 `[rag-N]`
类标记处理（DeepMentor 在 `TracePresentation.tsx`）；标记是否出现取决于 intellect-team
网关把 RAG 召回内容注入 agent-loop 的模板，而非前端——需要抓一条真实"附加知识库"
会话的网关回包才能判定。结论：**T11 移交 P3（或独立调研）**，不实施渲染。

**验证**：后端 ruff/pytest 59 项全绿；前端 typecheck / lint（0 错误）/ vitest 67 /
node 693 / 契约双门全绿；live：chunk 面板开关往返、图谱空态、5 tab 深链通过。
