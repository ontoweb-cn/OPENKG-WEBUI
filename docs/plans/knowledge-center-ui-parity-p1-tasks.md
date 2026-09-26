# 知识中心 UI 对齐 P1 细化任务清单（列表增强 + 任务中心 + 检索参数 + Settings 编辑）

- 日期：2026-09-26
- 状态：**已实施完成（2026-09-26）**——T5-T8 全部落地；T8 经 live 验证抓出并修复
  一处 engine 吞错误的真 bug（见 §六）
- 依据：[knowledge-center-ui-parity.md](knowledge-center-ui-parity.md) §四 P1；
  P0 已交付（[p0-tasks](knowledge-center-ui-parity-p0-tasks.md) §六实施记录）
- 前置：P0 的四 tab 骨架、预览抽屉、拖放上传、Settings 面板已在位

## 一、范围与不做

P1 = 方案 §四 T5-T8。**不做**：主从文件树（本阶段降级，见 R1）、embedding 信息展示
（D8：上游 `/embedding` 是 POST 触发器，留 P2-T10 探针定读取路径）、逐文件上传进度。

## 二、任务分解

### T5 文档列表增强（约 1.5 人日）

**落点**：`KnowledgeDetailPage.tsx`（DocumentTable 改造）、`api.ts`（fetchDocuments
接分页参数——**代理已支持** `page/page_size`，knowledge.py:321-332，纯前端）。

- 分页：`page_size=50` + 上一页/下一页 pager（total 由 `DocumentPage.total` 提供；
  fetchDocuments 返回 `{documents, total}` 已有）。翻页重置选中集。
- 搜索：名称子串过滤（**客户端**，对当前页；顶部搜索框，防抖 200ms）。
- 状态筛选：全部 / 完成 / 解析中 / 失败（客户端，基于 run）。
- 目录分组：按 `location` 的父目录插入分组头行（根级文档归 "根目录" 组）；
  平铺表格形态保留（**主从树降级到 P2**，R1）。
- **验收**：翻页/筛选/搜索/分组头渲染正确，选中集在翻页时清空；空结果给空态。

### T6 任务中心（约 1.5 人日）

**落点**：新增 `web/features/knowledge/components/ParseTasksPanel.tsx`，Documents tab
内、文档表之上；数据全部来自既有端点。

- 运行中区：RUNNING 文档逐条进度条（`doc.progress`）+ Stop parsing 按钮（从页头
  action 区保留，面板内不重复）。
- 历史区：`fetchIngestionLogs`（已有）列表——状态徽标 + 文档名 + 耗时/时间 + message
  尾行，可展开；刷新按钮。
- 失败恢复：一键"重试全部失败"（收集当前页 FAIL 文档 id → `parseDocuments`）；
  失败行内 Reparse 已有。
- **验收**：RUNNING 时进度条按轮询刷新；历史列表渲染真实 ingestions；重试触发解析。

### T7 检索试玩参数开放（约 0.5 人日，纯前端）

**落点**：`KnowledgeDetailPage.tsx` RetrievalPlayground、`api.ts`（searchDataset 加
options 参数，去硬编码——代理与引擎均已支持，knowledge.py:997-1013）。

- 参数 UI：`similarity_threshold`（0-1 步长 0.05）、`top_k`（1-1024）、
  `vector_similarity_weight`（0-1 步长 0.05）——number/range 输入 + 默认值与现状一致
  （0.2 / 1024 / 0.3，引擎签名缺省）。
- chunk 完整内容：`line-clamp-4` 加"展开/收起"切换。
- **验收**：改阈值后检索结果随之变化；参数随 URL 不持久化（会话级即可）。

### T8 知识库改名/描述（约 1.5 人日，后端四层联动）

**落点**：`engines/intellect_rag.py` 补 `update_dataset`（上游 `PUT /datasets/{id}`
接受部分 body `{name?, description?}`——DeepMentor client.py:273 已验证；
随后 `get_dataset` 回读返回域模型）；`routers/knowledge.py` 加
`PUT /datasets/{dataset_id}`（`response_model=KnowledgeDataset`，T3 信封归一口径）；
`contracts/export.py` 重导出 + `web contracts:generate`；Settings 面板加编辑表单。

- 表单：Name/Description 输入 + 保存（PUT 后用返回的域模型刷新页头标题与元数据）；
  校验：name 非空。
- **验收**：改名后列表页/详情页/页头同步更新；contracts 双门
  （`test_frontend_contract_export` + `contracts:check`）绿；`pytest tests/api/test_knowledge_router.py`
  增补 PUT 用例。

## 三、横切约束（同 P0 §三）

契约：T8 动 router 签名 → 四层同步 + 双门；T5-T7 纯前端不动契约。
i18n：新串全部 `t("…")` 字面量 + zh/en 同步。子路径规则沿用。

## 四、验收口径

- `npm run check:fast` 全绿；`pytest tests/api/test_knowledge_router.py` 全绿（含新增 PUT 用例）；
- live：翻页/筛选/分组、任务面板、检索参数、改名回读各验一例。

## 五、技术评审记录（2026-09-26）

### R1 主从文件树降级到 P2（范围修订，采纳）

原 P1-T5 含"T2 抽屉升级为主从面板"。评审结论：主从树的价值绑定文件夹操作
（新建/拖拽移动——本仓后端无此代理端点），只搬树形展示而无操作属半成品；
且分组头 + 筛选已解决"100 条平铺难用"的主要痛点。**修订**：P1 交付分组表格 +
筛选 + 分页；主从树随文件夹操作（需上游 raw/ 目录管理端点调研）一并入 P2 另行细化。

### R2 筛选/搜索为客户端口径（澄清）

翻页在服务端，搜索与状态筛选在**当前页**客户端执行——二者组合的语义是
"先服务端翻页，再页内过滤"。跨页全量搜索需要上游 list API 支持 filter 参数
（未验证），不冒进。页脚注明"当前页内过滤"。

### R3 T8 更新回读必须 get_dataset（采纳 DeepMentor 模式）

PUT 后直接信任请求体会造成归一化字段漂移（visibility/counts）；回读
`get_dataset` 以服务端状态为准，与 DeepMentor client.py:2655-2661 同款。

### R4 任务面板数据边界（澄清）

ingestions 是**解析任务日志**（`log_type=file`），非上传任务；面板命名
"解析任务"（ParseTasksPanel），避免暗示上传队列。历史取 20 条（现有缺省）。

### R5 T7 参数范围钉死（防 UI 面板失控）

只开放三个参数（threshold/top_k/vector_weight），与引擎签名一一对应；
不做方法选择器（上游 search 是否支持 retrieval mode 参数未验证——留 T10 探针）。

### 评审结论

**通过**。R1 为范围修订（主从树 → P2），R2/R4/R5 为口径钉死，R3 为实现要求。

## 六、实施记录（2026-09-26）

**T5**：`fetchDocuments` 接服务端分页（page_size=50 + total）；名称过滤（页内）+ 状态
筛选（全部/完成/解析中/失败）chip 组；`location` 父目录分组头（`GroupRows`）；
`PagePager`（单页隐藏）。live：状态筛选空态、名称过滤收窄均通过。

**T6**：新增 `ParseTasksPanel`——RUNNING 文档进度条 + 失败批量重试 + ingestions 历史
（可展开看日志尾）；Refresh 手动重拉（logTick）；空闲且有历史时显示折叠历史
（测试预期修正：有历史就该显示，非 bug）。

**T7**：`searchDataset` 参数化（similarity_threshold/top_k/vector_similarity_weight，
缺省=引擎口径）；"检索参数"折叠面板 + chunk 展开/收起。live：参数面板打开、带参数
检索返回命中。

**T8**：后端 `engine.update_dataset`（PUT 后 `_unwrap` + get_dataset 回读）+
`PUT /datasets/{id}` 路由（same-origin、空 body/空 name 400）+ 契约重导出/重生成 +
2 项新 pytest（回读口径、本层校验）；前端 Settings 编辑表单（键控草稿派生，
避免 effect 内 setState——lint react-hooks v6）。

**live 验证抓出的真 bug（已修）**：初版 `engine.update_dataset` 未解包 PUT 响应——
上游对无权限数据集返回 `code 102 "User 'x' lacks permission"`（tenant 级 owner 校验，
update 严格于 GET 的 accessible），被吞后回读旧数据**假成功**。修复后错误如实映射为
`knowledge_unauthorized`（401/403 族）。正向用例：`联调测试库(1)` 改名往返成功；
`3D场景图预测论文`（他者租户创建）改名收到明确权限错误——符合"owner 才可改"语义。

**验证**：后端 59 项（knowledge 路由+服务）+ ruff 全绿；前端 typecheck / lint（0 错误）/
vitest 67 / node 693 / contracts 双门全绿。live（:8083 临时后端 + dev 前端）：
筛选/分组/参数检索/改名往返/权限拒绝浮出全部通过。

**P0 遗留项跟进**：Settings 元数据的 Token 数/创建时间显示"—"——上游 dataset 行
`token_num` 有值但代理域模型 `token_count=0`（字段映射待核），创建时间为空串；归
P2 与 chunk 探针一并处理。
