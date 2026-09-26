# 知识中心 UI 对齐 P3 立项方案：长线四项细化（多引擎 UI / 索引兼容性检查 / 进度文案本地化 / 引用标注）

- 日期：2026-09-26
- 状态：**评审通过（两轮，§八/§九）**——四项相互独立，按 §五 排期逐项实施
- 依据：[knowledge-center-ui-parity.md](knowledge-center-ui-parity.md) §四 P3；
  P0-P2 与收尾批次已交付（其任务文档的实施记录）
- 前置调研（本轮完成，均有源码/实测证据）：

| 项 | 调研手段 | 关键结论 |
| --- | --- | --- |
| T12 多引擎 UI | `engines/registry.py`/`base.py` 现状 + T5 方案文档 | **T5（KAG 第二引擎）细化方案已存在**（推荐 B：统一目录+分派详情，含引擎目录模型与 provider 映射表）；本项 = T5 的 UI 实施侧，不另起炉灶 |
| T13 索引版本管理 | 上游 `dataset_api_service.check_embedding` 源码 + dataset 行字段 | 上游每 dataset **单索引**（`embd_id` 列），无多版本存储——DeepMentor 的版本管理建立在其本地多索引上，代理形态不适用。**收窄为"换模型兼容性检查"**：`POST /embedding/check` 是抽样重嵌入算余弦的只读探针（不重建索引） |
| T14 进度文案本地化 | DeepMentor `progress_tracker.py`/`knowledge-helpers.ts` + 上游 `task_service.py` | 上游 `progress_msg` 是**追加式自由文本**（3000 行截断），无 message_key 机制——DeepMentor 的 message_key 由其后端自产。结构化字段（徽标/百分比）本地化 P1 已达成 |
| T11 引用标注 | 网关源码 `plugins/rag/intellect-rag/__init__.py` + `agent/rag_manager.py` | **根因确定**：网关 `_render_search_result` 渲染 `[{doc}]\n{content}`，**无 `[rag-N]` 编号标记**——openkg 链路不会出现标记（DeepMentor 的标记来自其本地管线模板）。属跨仓改造（intellect-team 网关仓） |

## 一、T12 多引擎 UI（2~3 人日 UI + T5 后端另计）

**定位**：T5 方案（[knowledge-center-phase-3-t5-kag-engine.md](knowledge-center-phase-3-t5-kag-engine.md)）的后端 provider 部分按其既有细化实施；本方案只补其未覆盖的 **UI 侧细化**。

### 任务

| # | 任务 | 落点 |
| --- | --- | --- |
| 12.1 | 引擎目录端点：`GET /api/knowledge-center/engines`——遍历 `registry`，序列化 `{engine_id, display_name, capabilities[], configured, kb_count, detail_path_template}`（T5 §3.2 的目录模型）。**健壮性（评审二轮 R6）**：单引擎的 kb_count 为 best-effort——其列表调用失败置 `kb_count: null` + `error` 字段，不得拖垮整个目录响应 | router + 契约 |
| 12.2 | KC 首页"引擎"分组网格：引擎卡（名称/能力徽标/KB 数/configured 态），点击按 `detail_path_template` 分派（intellect-rag → 本页；KAG → `/kag/projects`）——T5 推荐 B 的"统一目录" | `KnowledgeHomePage` |
| 12.3 | 创建流程引擎选择：新建对话框第一步选引擎（未 configured 的引擎卡禁用 + 原因文案），选 KAG 时跳既有 KAG 创建页（不复制其表单）——**入口已核实存在**（`KagProjectsPage` 内 `CreateProjectDialog` + `createKagProject`，评审二轮） | `CreateDatasetDialog` |
| 12.4 | 详情页引擎徽标：dataset 卡与详情头显示引擎名（数据来自目录端点按 engine_id 反查） | 列表卡 + 详情头 |

### 验收

- 目录端点返回两引擎（intellect-rag configured=true；KAG 按 `kag_enabled()`）；
- 首页网格/创建分派/徽标三处渲染正确。
- **能力映射表的验收口径（评审二轮 R7）**：分派模式下 KAG 不进 KC 详情页（跳
  `/kag/projects/{id}`），因此能力门（按 capabilities 裁剪 tab）在只有 intellect-rag
  一个 in-KC 引擎的现实下**无法被集成验证**——映射表落码 + 单测（CAP_* ↔ tab 映射），
  集成验证留给第三个 in-KC 引擎；本项验收以目录端点与分派流为准。

### 依赖与风险

- **硬依赖**：T5 方案评审通过 + 其 provider（`engines/kag.py`）落地——UI 无 provider 可列出时只显示 intellect-rag，不阻塞本项开发（目录端点天然兼容单引擎）。
- 风险：能力集合与 UI tab 的映射需防漂移（CAP_* 常量即契约，UI 侧建映射表并有单测）。

## 二、T13' 索引兼容性检查（1.5~2 人日）

**定位**：原"索引版本管理"收窄——上游无多版本存储，可交付的是**换嵌入模型的兼容性检查与引导**。实施无需再探针（返回形状已源码钉死，见 13.1；模型清单形状已 live 实测，见 13.0）。

### 任务

| # | 任务 | 落点 |
| --- | --- | --- |
| 13.0 | **嵌入模型清单端点**（评审二轮新增）：`GET /api/knowledge-center/models?type=embedding` 透传上游 `GET /api/v1/models`（实测返回 instance 列表：name/provider_name/model_type）——更换流程的候选来源，替代原"文本输入 embd_id"（R5） | router + 契约 |
| 13.1 | engine `check_embedding_compatibility(dataset_id, embd_id, check_num=5)` → `POST /embedding/check`；**返回形状已按源码钉死**：`{model, sampled, valid, avg_cos_sim, min_cos_sim, max_cos_sim, match_mode, results:[{chunk_id, doc_name, vector_dim, cos_sim, reason?}]}`；维度不匹配上游直接报错（"dimension … different"）→ 归一为不兼容结论。兼容判定阈值本仓定：`avg_cos_sim ≥ 0.6`（D6，可调） | engine + models |
| 13.2 | engine `update_dataset` 支持 `embedding_model` 参数（上游 PUT 已支持该字段）；**换模型前强制走 13.1** | engine |
| 13.3 | Settings 嵌入模型行升级：当前模型 + "更换"流程（**下拉选 13.0 的候选** → 兼容性检查结果展示（avg/min/max + 抽样数）→ 兼容才允许保存；不兼容给出重建指引：删除重建或重新解析） | Settings 面板 + 契约 |
| 13.4 | 单测：mock 上游 check 三态（success/not_effective/error）+ 维度不匹配错误归一 | tests |

### 验收

- live：`GET /models?type=embedding` 返回 embedding 候选（至少含 qwen3-embedding-4b）；
- live：对联调测试库执行一次兼容性检查（embd_id 用当前模型自检 → compatible=true，
  avg_cos_sim 接近 1、sampled≥1）；
- 不兼容/维度不匹配/上游错误三路径以 mock 单测覆盖（13.4）；
- 契约四层联动（13.0 新端点 + check 结果/models 实体）+ 双门绿；
- 更换流程空态：模型清单为空（上游未配置 embedding 模型）时显示引导文案
  （"请在 RAG 服务端配置嵌入模型"），不渲染下拉。

### 风险

- `/embedding/check` 消耗 embedding 算力（`check_num` 默认 5 条重嵌入）——UI 明示
  "将抽样重嵌入"，检查按钮二次确认。
- 换模型后**存量 chunk 向量不自动重建**（上游行为）——UI 必须强提示"需重新解析文档
  以重建向量"（D5）。
- 兼容阈值是产品口径而非上游语义——`COMPAT_THRESHOLD = 0.6` 常量置于 engine 模块
  一处（D6），调整不动 UI。

## 三、T14' 进度文案本地化（0.5 人日，收窄）

**定位**：上游无 message_key 机制（追加式自由文本），DeepMentor 模式不可照搬。结构化字段（状态徽标/百分比）P1 已本地化——本项只做**自由文本的最小映射**。

### 任务

| # | 任务 | 落点 |
| --- | --- | --- |
| 14.1 | `progressMessage(raw, t)`：pattern → message_key 最小映射表（≤10 条：`[ERROR]`/`Exception` → "解析出错"；`task finished`/`done` → "解析完成"；`running`/`parsing` → "解析中"…），无命中原样返回 | `model.ts` 纯函数 + 单测 |
| 14.2 | ParseTasksPanel / SSE 日志行接入（仅状态行，日志正文原样） | 组件 |

### 明确不做

- 上游侧 message_key 机制改造——属 rag-app 仓库的提案（模板化 progress 事件），记入上游协作清单，不在本仓。

## 四、T11 引用标注（跨仓改造提案，G1+G2 合计 2 人日）

**定位**：标记缺失的根因在网关注入模板（确定性结论，不再是"待抓回包"）。拆两段：

### G1 网关注入编号（intellect-team 仓，1 人日）

- `plugins/rag/intellect-rag/_render_search_result`：chunk 渲染改为 `[rag-N] [{doc}]\n{content}`（N 为序号）；
- `agent/rag_manager.build_rag_context_block`：block 头部追加指示："回答时按 `[rag-N]` 标注引用来源，无依据不要编造编号"；
- **可行性已核实（评审二轮 R8）**：`sanitize_rag_context` 的三个正则（`_FENCE_TAG_RE`/`_INTERNAL_CONTEXT_RE`/`_INTERNAL_NOTE_RE`）只剥 `<rag-context>` 包裹与 `[System note: …]` 信封，**不触及 `[rag-N]` 编号**——标记可存活进入 prompt；
- 网关仓自测：prefetch 输出含编号；prompt 指示生效（一条真实 turn 的回包含 `[rag-1]`）。

### G2 前端渲染（本仓，1 人日）

- `CITATION_MARKER_RE` 识别 `[rag-N]` → 上标 citation 徽标（hover 显示来源文档名——来源映射需网关在块尾附带编号→文档名清单，G1 一并输出）；DeepMentor 的正则本就覆盖 `rag|web|paper|code|src` 前缀，移植时保留本仓实际会出现的 `rag` 子集；
- 移植 DeepMentor `RichMarkdownRenderer.tsx:535-564` 的渲染模式至本仓聊天渲染链路。

### 依赖与风险

- **G2 依赖 G1 合入发布**（网关不产标记则渲染无从触发）——G1 属上游仓，节奏不受本仓控制；
- 降级安全：无标记时 G2 渲染器零副作用（正则不命中即原样）。

## 五、排期建议

```
T14'（0.5d，独立）→ T13'（1.5~2d，独立，R5 增补 13.0）→ T12（2-3d UI，随 T5 评审/实施）→ T11（G1 上游仓 → G2 本仓）
```

四项无相互依赖；T12 与 T5 合并为一个实施波次最高效（T5 的验收项天然包含 12.2-12.4 的场景）。

## 六、决策记录

| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | T12 按 T5 方案实施，本方案只补 UI 细化 | T5 已有完整 provider 映射与目录模型，重复设计有害 |
| D2 | T13 收窄为兼容性检查，不做版本存储 | 上游单索引；代理形态造不出真版本 |
| D3 | T14 收窄为最小映射表（≤10 条） | 上游无 message_key；全量翻译自由文本不可行且脆弱 |
| D4 | T11 拆 G1（网关仓）/G2（本仓），G2 依赖 G1 | 根因在网关模板；单仓无法闭环 |
| D5 | 嵌入模型更换必须先过兼容性检查 | 换模型后存量向量不自动重建（上游行为），防"换完即检索退化" |
| D6 | 兼容判定阈值 `avg_cos_sim ≥ 0.6`，本仓可配 | 上游只回统计不判兼容；阈值是产品决策，默认值取 RagFlow 生态惯用口径，集中常量便于调 |

## 七、风险与缓解

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| `/embedding/check` 算力消耗（抽样重嵌入） | 中 | UI 二次确认 + 明示抽样行为 |
| 换嵌入模型后存量检索退化 | 高（数据性） | D5 强制检查 + 重建指引文案 |
| T12 能力映射漂移 | 低 | CAP_* 即契约 + 映射表单测 |
| G1 上游仓排期不可控 | 中 | G2 渲染器零副作用，可先合入等标记 |
| T14' 映射表对上游文案变化脆弱 | 低 | 无命中原样兜底；映射表集中一处便于维护 |

## 八、技术评审记录（2026-09-26）

评审方式：源码级调研（本仓引擎层、上游 rag-app 数据面、DeepMentor 两处参照实现、
intellect-team 网关 RAG 插件）逐项核对原 P3 设想，四项全部据调研修订。

### R1 T12 与 T5 合并（实质修订，采纳）

原设想"移植 DeepMentor EngineDetail"——T5 文档已细化第二引擎方案且明确推荐
"统一目录+分派"而非复刻 EngineDetail 全量（引擎级检索配置表单等 DeepMentor 专属
面不迁移）。本方案据此把 T12 收窄为 T5 的 UI 实施侧，删除"引擎配置表单"相关设想
（检索参数走 per-库 + 会话级，引擎级配置面与既有决策冲突）。

### R2 T13 实质重定义（采纳）

"索引版本管理"在上游单索引事实下不可实现；`check_embedding` 源码证实是**只读
兼容性探针**（抽样重嵌入算余弦，非重建触发器——与 D8 的谨慎判断一致并最终闭环）。
重定义为"换模型兼容性检查 + 重建引导"，并把 `embedding_model` 更新纳入既有
`update_dataset` 通道。

### R3 T14 实质收窄（采纳）

上游 `progress_msg` 为追加式自由文本（`task_service.py:135` 拼接、3000 行截断），
无结构化消息机制；DeepMentor 的 message_key 由其后端自产（`progress_tracker.py:166`）。
结构化字段本地化已在 P1 达成，剩余自由文本仅做最小映射。

### R4 T11 从"待调研"转"跨仓提案"（采纳）

网关源码给出确定性答案：`_render_search_result`（`__init__.py:380-414`）渲染
`[{doc}]\n{content}`，无编号标记——openkg 链路（经 intellect-team 网关）不会出现
`[rag-N]`；DeepMentor 的标记由其本地管线模板生成。P2 的 no-go 结论升级为明确的
两段式改造提案（G1 网关 + G2 前端）。

### 评审结论

**通过**。四项均按调研实质修订（R1/R2/R3 重定义、R4 转提案）；每项相互独立、
可单独排期。实施前无需再调研——各项的验收口径见 §一-四。

## 九、补充评审（第二轮，2026-09-26）

首轮评审（§八）基于调研结论重定义了四项。第二轮对方案中**悬空的技术断言**逐条
取证（上游服务源码 + 网关正则 + 前端组件现状），发现并修订如下：

### R5 更换模型需要候选清单端点（计划缺口，新增 13.0）

原 13.3 的"输入新 embd_id"文本输入不成立——用户无从得知合法 embd_id（形如
`qwen3-embedding-4b@default@GPUStack`）。上游 `GET /api/v1/models` 返回 instance
清单（name/provider_name/model_type），新增 13.0 代理端点（type=embedding 过滤），
更换流程改为下拉选择。**13.0/13.3 均为契约面改动**（新增端点 + models 实体化），
契约四层联动成本已计入。

### R6 目录端点健壮性（采纳进 12.1）

目录端点聚合各引擎的列表调用——单引擎失败（如 KAG 未配置/超时）不得以 5xx 拖垮
整个目录。kb_count 改 best-effort（失败置 null + error 字段）。

### R7 能力门验收弱化（澄清）

分派模式下 KAG 不进 KC 详情页，能力门在单 in-KC 引擎现实下无法集成验证。验收
改为"目录端点 + 分派流"；CAP_*↔tab 映射表落码 + 单测，作为第三引擎的预留。

### R8 G1 可行性确认（消除 T11 残留风险）

`sanitize_rag_context` 三正则逐一核对——只剥 `<rag-context>` 包裹与
`[System note: …]` 信封，`[rag-N]` 编号可存活进 prompt。T11 的"标记是否会中途
被剥"风险解除，G1 可直接实施（待上游仓排期）。

### R9 check_embedding 返回形状钉死（细化 13.1）

源码实测：请求可带 `check_num`（默认 5，从最多前 1000 条 available chunk 随机采样）；
返回 `{model, sampled, valid, avg/min/max_cos_sim, match_mode, results[…]}`；
**维度不匹配在上游直接抛错**（"dimension … different"）→ 归一为"不兼容（维度）"
而非笼统失败。13.1 的归一模型由假设形状改为实测形状；兼容阈值本仓定
（D6：avg_cos_sim ≥ 0.6）。

### 第二轮结论

**通过（修订版）**。R5 为计划缺口补全（新增 13.0），R6/R7 为健壮性与验收口径，
R8/R9 消除残余不确定性。四项实施无需再调研；T13' 的工作量因 13.0 上调至
**1.5~2 人日**，其余不变。
