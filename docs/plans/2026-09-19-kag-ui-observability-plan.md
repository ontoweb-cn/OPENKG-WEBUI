# KAG UI 完善开发计划（构建任务可观测性 P0a）

状态：**已归档**（2026-09-20 全部交付，见 §8；早期方案评审见 §7，依据
`docs/kag-management-ui-gap-analysis.md` 结论制定）
日期：2026-09-19
合规：openspgapp 仅作**行为级参考**（不包含、不拷贝、不翻译其代码，同
`kag-integration-design.md` §1/§12）；REST 契约以开源 knext 客户端为权威，
实现一律走 OpenSPG `/public/v1/*` 公开接口（与既有 `openspg_client.py` 一致）。

## 1. 背景与定位

- openkg-webui `/kag` 是**管理/运维薄层**（代理 OpenSPG `/public/v1/*`）。
- gap 分析结论：唯一「收益即刻、独立于上游」的缺口是**构建任务可观测性
  （P0a）**——当前 `submit_build` 仅为受理层，任务状态冻结在提交时的
  `status=RUNNING`（`task_store.py` 的 `answer_digest` 只写一次），
  「受理成功 vs 执行失败」不可见。
- 构建闭环（P0b）、数据导入执行均依赖 M5 上游化后配置 executor，本计划**不投入**
  （后续经 §8 独立排期交付 P0b，见 §2.2 状态列）。

## 2. 范围与原则

### 2.1 做什么

阶段 A（本计划主交付，独立可交付）：**构建任务可观测性**。

### 2.2 不做什么（明确排除）

| 项 | 理由 | 当前状态（2026-09-20 计划完结时） |
| --- | --- | --- |
| P0b 构建闭环（真正执行成功） | 依赖 M5 上游化 executor 可配置（gap §6、设计风险 #5）；源码核实详情见 2.3 | ✅ 已交付——经 §8 独立排期（M0–M3：local driver + 真实 `kag builder` 闭环，§8.16/§8.18 生产零 openspgapp 依赖） |
| 图浏览改造 | reason DSL 是 M2 既定方案，勿返工 | ✅ 已交付——阶段 C 的 C1 以增量方式完成（§5.1：实体详情/一跳图，勿返工约束未破） |
| App 编排 | 产品决策项（MVP 边界），勿仅因 openspgapp 有而补 | ⏳ 仍排除——待产品决策（§4 标注） |
| 多租户完整授权 | T3 可选演进 | ⏳ 仍排除——T3 排期（§4 标注） |

> 状态列说明：本表保留计划制定时的**原始排除决策**（历史记录不变）；P0b 与图浏览
> 并非在阶段 A 内补做，而是分别经 §8 独立排期、§5.1 C1 交付，故原排除理由仍成立。

### 2.3 P0b 阻塞详情：computing engine driver（2026-09-20 源码核实）

**角色**：构建提交后，调度器把执行交给
`ComputingEngineAsyncTask.submit`（openspgapp
`.../scheduler/service/task/async/builder/ComputingEngineAsyncTask.java`）：
`getClient(cloudext.computingengine.url)` → `client.submitBuilderJob(job, ext)`
→ 轮询 `queryStatus`（RUNNING/SUCCESS/FAILED/NOTFOUND，自动重试/终止）。
computing engine = **真正执行 `kag builder` 命令的执行后端**；extension 携带
COMMAND、graphstore/searchengine URL、python 路径、project JSON 等。

**报错根因（两层）**：
1. URL 为空：server 配置键 `cloudext.computingengine.url`
   （DefaultValue.java，默认空串）。
2. **开源分支无 driver 实现**：`ComputingEngineClientDriverManager` 按 URL
   scheme 匹配已注册 driver（`loadDrivers("cloudext.cache.drivers", ...)`，
   该键疑为上游笔误）；而 `cloudext/impl/` 仅有 graph-store/cache/
   search-engine/object-storage——**无 computing-engine 实现目录**。即使设
   置 URL，classpath 也无 driver 类可匹配 scheme。

**与 Neo4j 部署的关系（独立）**：Neo4j 部署只修复查询侧（图浏览/详情/一跳
数据路径）；构建执行走 scheduler→computing engine 另一后端，图库健康与否
不影响 `getClient` 报错。标准 release compose（server/mysql/minio/neo4j）
本身不含 computing engine 服务。

**闭环所需**：
1. 配置 `--cloudext.computingengine.url=<scheme>://host:port`（与 release
   compose 给 graphstore 传参同机制）。
2. **driver 实现（核心缺口，开源未提供）**：官方形态（OpenSPG 上游提供
   computing engine driver/executor——注意与 M5-B 的 MCP 推理执行面**不是
   同一条线**，属另一上游组件）或自研本地 driver（实现
   ComputingEngineClientDriver + ComputingEngineClient 的
   submitBuilderJob/queryStatus/stop，子进程跑 `kag builder` 并轮询）。
   中等工程量 + 契约对齐。
3. worker 侧环境：KAG 项目（git 仓库/entry_script）、`kag` CLI、
   LLM/vectorizer、graphstore 可达（extension 自动携带 URL）。

**结论**：P0b 不是"再部署一个容器"可解决——开源缺的是 computing engine
driver 本身（接口有、实现无），属上游组件边界。openkg-webui 受理层 + P0a 可观测
已将该能力最大化。

**上游线澄清（2026-09-20 核实）**：M5-B（KAG `kag mcp-server` 冻结契约
kag-solve/kag-schema/kag-reason/kag-status，feat/mcp-frozen-contract 已推
simongu/KAG）是**推理/求解执行面**——chat 的 grounding/求解经 .mcp.json
双宿主接入，已交付、openkg-webui 零改动。它与 P0b 的**构建执行**是不同概念：
P0b 需要 OpenSPG server 侧 `cloudext.computingengine.*` driver 实现（开源
缺失），独立排期——等 OpenSPG 上游提供，或自研本地 driver。

## 3. 阶段 A 任务拆解

**目标**：提交构建后，任务列表/详情实时反映 OpenSPG 侧执行状态（受理中 /
执行中 / 成功 / 失败），失败可见节点级日志（traceLog）；OpenSPG 不可达时
优雅降级为本地受理摘要。

**数据源（OpenSPG 公开 REST，经既有代理模式）**：

| 端点 | 用途 |
| --- | --- |
| `GET /public/v1/builder/getById?id=` | `BuilderJob`（`status`/`taskId`=SchedulerJob id/`jobName`/`gmtCreate`） |
| `POST /public/v1/builder/search` | 按 projectId 批量查 BuilderJob（列表合并用） |
| `POST /public/v1/scheduler/instance/search` | 按 `jobId` 查 `SchedulerInstance` |
| `POST /public/v1/scheduler/task/search` | 按 `instanceId` 查 `SchedulerTask[]`（每节点：`title`/`type`/`status`/`traceLog`/`output`） |

### A1 后端

- [x] **A1.1 `openspg_client.py` 新增 4 个 client 方法（信封解包）**
  `openkg_webui/services/kag/openspg_client.py`：
  - `get_builder_job(job_id)` → `GET /public/v1/builder/getById`
  - `search_builder_jobs(project_id, page_size=100)` → `POST /public/v1/builder/search`
  - `search_scheduler_instances(job_id)` → `POST /public/v1/scheduler/instance/search`
  - `search_scheduler_tasks(instance_id)` → `POST /public/v1/scheduler/task/search`
  - **响应信封（评审 P1-2，已核实）**：builder/scheduler 控制器走
    `HttpBizTemplate.execute2`，响应为 `{result: <payload>}` 信封——与
    project/reason 端点的裸对象响应（现有 `list_projects`/`reason_run`
    直接取值）不同。4 个新方法统一解包 `result`；`search` 的 `result` 是
    分页体 `{results, pageNo, pageSize, total}`，解到 `results` 列表。
  - 沿用 `_request` 模式（JSON 响应、`OpenSPGError` 包装、`_parse_response`
    兜底）；防御 server 偶发空体（归一为 dict/list）。

- [x] **A1.2 `task_store.py` 扩展 build 记录**
  `openkg_webui/services/kag/task_store.py`：
  - `_normalize` 增补可空字段：`scheduler_job_id`、`status`（旧记录缺失时
    回填空串，列表仍可渲染）。`scheduler_job_id` 保持**可选**（评审 P2-3：
    详情端点惰性回填，不阻塞写入）。
  - 新增幂等更新 `update_task(task_id, **fields)`（复用现有 `_lock` 滚动截断
    逻辑，按 task_id 覆盖）。

- [x] **A1.3 `kag.py` 新路由**
  `openkg_webui/api/routers/kag.py`（全部走既有 `_require_project_access` +
  membership 过滤 + same-origin 写操作；响应过 `_sanitize` 凭据掩码）：
  - `GET /projects/{project_id}/builds` — 项目构建列表：本地 build 记录 ×
    实时 `BuilderJob.status` 合并（`search_builder_jobs` 批量映射，jobId 对应
    task_id）；OpenSPG 不可达时降级返回本地摘要并标注 `live_status="unknown"`。
  - `GET /builds/{job_id}` — 详情：`get_builder_job` → `taskId` →
    `search_scheduler_instances` → 首个 instance → `search_scheduler_tasks`，
    组装节点级 `{name, type, status, traceLog}` 列表（traceLog 只读、截断 2k）。
    归属校验：经 `project_id` 查 membership（非 admin 仅本项目可见）。
  - 状态归一（A1.4 实测修正）：`BuilderJob.status` **恒 `RUNNING`**（不随执行
    更新），实例级 `status` 可能停在 `WAITING`（DAG 未完）——**成败判定取
    节点级聚合**：任一节点 `ERROR`→failed、全部 `FINISH`→success、有
    `RUNNING`→running、其余（WAITING/WAIT）→pending；无 taskDag 时兜底
    Job.status。节点状态来源：`taskDag.nodes[].properties.status`（列表用）
    或 `SchedulerTask.status`（详情用）。
  - **扩展 `GET /tasks`（评审 P1-1）**：对 build 记录做 live 状态合并——
    `asyncio.gather` **并行**逐条 `get_builder_job`（≤100 条，逐条容错，
    失败降级 `live_status="unknown"`）；inference 行不受影响。任务页
    （`/kag/tasks`）与项目页共用该状态口径。
  - **提交侧不改（评审 P2-3）**：不做提交后回查——详情端点经
    `get_builder_job` 惰性拿到 `taskId`（提交响应体的 BuilderJob 可能不含
    taskId，A1.4 实测确认），`scheduler_job_id` 仅作缓存。

- [x] **A1.4 契约实测（2026-09-19 完成）**
  对已受理任务（job id=1/2，`echo m0-probe`/`echo m4-build-probe`）curl
  `/public/v1/builder/getById`、`/scheduler/instance/search`、
  `/scheduler/task/search`，结论：
  - **`BuilderJob.status` 恒 `RUNNING`**，不随执行更新（昨日提交今日仍
    RUNNING）——仅作「已受理」标识，非状态权威。
  - **失败信号在节点级**：`instance/search` 响应内嵌 `taskDag.nodes[]`
    （`Builder`=computingEngineAsyncTask 已 `ERROR`，`PostProcessor`=
    kagCommandPostSyncTask `WAIT`）；实例级 `status` 停在 `WAITING`
    （DAG 未全终态），**不能**直接映射成败。
  - **traceLog 形态**：`task/search` 每节点一条，`traceLog` 为可读文本
    （时间戳+堆栈）；`cannot find driver for` 出现在 `Builder` 节点
    `ComputingEngineClientDriverManager.getClient`（设计风险 #5 实证）；
    失败任务被调度器**反复重试**（`executeNum=378`），traceLog 持续追加
    增长，展示须截断。
  - 响应均为 `{result: ...}` 信封；`search` 分页字段为 `pageIdx/pageSize/total`。

- [x] **A1.5 后端测试**
  - 新增 `tests/services/kag/test_build_observability.py`：`MockTransport`
    注入式 client 测试（4 个新方法 × 正常/404/空体）+ 路由测试
    （列表合并、状态归一、详情组装、OpenSPG 不可达降级）。
  - 扩展 `tests/services/kag/test_m4b_acl.py`：新端点 membership 门禁
    （非 admin 跨项目 403、admin 放行）。
  - 新端点路由须通过 `tests/api/test_frontend_contract_export.py` 的
    operation_id **唯一性**断言（评审 P3-5）；同步重新生成已提交的
    `openapi.json` 与前端 TS 类型（项目惯例：新端点必须手动更新并重新生成
    前端契约）。

### A2 前端

- [x] **A2.1 `model.ts` 新增类型与解析**
  `web/features/kag/model.ts`：`KagBuildStatus`（`live_status` 归一值 +
  `answer_digest` 兜底）、`KagBuildNode`（`name/type/status/traceLog`）、
  `parseKagBuilds` / `parseKagBuildDetail`（沿用宽松归一 + 安全缺省模式）。
- [x] **A2.2 `api.ts` 新增调用**
  `web/features/kag/api.ts`：`fetchKagBuilds(projectId)`、
  `fetchKagBuildDetail(jobId)`（走 `apiUrl` 相对路径，`scope: "kag"`）。
- [x] **A2.3 任务列表页实时状态**
  `web/features/kag/components/KagTasksPage.tsx`：build 行显示状态 badge
  （执行中/成功/失败/未知，替换冻结的 `status=RUNNING` digest）；展开区对
  build 任务渲染节点状态列表 + traceLog（只读，等宽字体）；刷新以手动为主，
  10s 轮询可选（评审 P3-6，最小改动）。
- [x] **A2.4 构建受理面板回显**
  `web/features/kag/components/MemberBuildPanel.tsx`：提交成功后展示返回的
  jobId + 「在任务列表查看」入口（保留现有成功/错误处理，不新增复杂度）。
- [x] **A2.5 i18n**
  新增状态/日志相关 key（英文原文即键，zh/en 双语），约 8–12 个，沿用
  M4 增量模式。
- [x] **A2.6 前端测试与构建**
  - 扩展 `web/tests/kag-model.test.ts`（`parseKagBuilds`/`parseKagBuildDetail`
    缺字段/漂移降级）。
  - 全量构建校验（项目惯例：含原生依赖时必须跑完整 build，不只 check:fast）。

### A3 验收

1. 后端：`tests/services/kag/` 全绿（沿用 1700+ 后端测试基线，实测 **1757 passed /
   6 skipped**），`check:fast` exit 0。
2. E2E 冒烟（2026-09-19 完成，浏览器实测）：项目详情提交 `echo p0a-smoke` →
   面板显示「构建已受理 + 在任务列表查看」→ `/kag/tasks` build 行显示
   **失败** badge → 展开显示节点（`computingEngineAsyncTask`/
   `kagCommandPostSyncTask`）与 traceLog（可见 "Scheduler execute failed"）。
3. OpenSPG 不可达：任务列表正常渲染，build 状态标注「未知」、**inference 行
   不受影响**、不 500（评审 P2-4）。
4. 权限：非 admin 跨项目访问新端点返回 403。

## 4. 阶段 B — 数据导入（B-1 引导式 KAG_COMMAND 导入，受理层）

状态：**已评审**（2026-09-19 范围决策：B-1；前置核实见下）。

### B-0 前置核实（2026-09-19 完成）

- openspgapp `DatasController` 为 `v1/datas` 内部路径——**无公开文件上传端点**。
- knext 客户端 `BuilderClient.submit()` 是**未实现 stub**（`pass`）——"以 knext
  为权威"的导入提交契约不存在；`write_graph` 是写图接口，非数据接入。
- 数据接入公开契约仅两条路：**KAG_COMMAND**（`/public/v1/builder/kag/submit`，
  阶段 A 已实现提交+可观测）与完整 `BuilderJob` submit（`/public/v1/builder/job/
  submit`，需 pipeline+extension.extractConfig+fileUrl，重契约、无 knext 支撑）。
- **结论**：本阶段做 B-1（引导式 KAG_COMMAND 导入受理层），不做 B-2 完整
  BuilderJob（契约重、执行同样依赖 executor）；文件上传明确排除（无公开端点）。

### B-1 任务拆解（前端为主，后端零改动）

目标：项目详情页新增「数据导入」卡片——常见 KAG builder 命令模板预设 +
自由编辑 + 提交（复用 `POST /projects/{id}/build`）+ 受理回显与任务列表入口
（复用阶段 A 可观测）。UI 明确标注：受理层、执行依赖远程 executor、数据须
放在 executor 可达位置（本面板不承载文件上传）。

- [x] **B1.1 模板预设与命令生成**
  `web/features/kag/`：定义导入模板常量（基于本 fork `kag builder` CLI 真实
  语法，[KAG/kag/bin/commands/builder.py](../../../KAG/kag/bin/commands/builder.py)）：
  - 结构化数据（Git 仓库）：`kag builder --project_id {projectId} --git_url
    <data-repo-url> --commit_id <commit-id>`
  - 非结构化文档（Git 仓库 + 入口脚本）：`kag builder --project_id {projectId}
    --git_url <data-repo-url> --commit_id <commit-id> --entry_script <run-import.py>`
  - 模板标注「示例，请按环境调整」；`{projectId}` 自动填充，`<...>` 占位待填。
- [x] **B1.2 前端「数据导入」卡片 `KagImportPanel.tsx`**
  `web/features/kag/components/KagImportPanel.tsx`：说明文案（受理层/无上传/
  executor 依赖）+ 模板 chips（点击填入命令输入框）+ 可编辑命令输入 +
  提交按钮（复用 `submitKagBuild`）+ 成功回显 taskId + 「在任务列表查看」；
  与构建卡片语义区分（构建=任意命令，导入=引导模板），共享提交实现不重复。
  `KagProjectDetailPage.tsx` 在 `<MemberBuildPanel/>` 后渲染。
- [x] **B1.3 i18n**：新增导入相关 key（zh/en，约 6–8 个），沿用英文原文即键。
- [x] **B1.4 前端测试与构建**：`web/tests/kag-model.test.ts` 无新解析（不新增
  后端解析）；跑 `check:fast` + 完整 `npm run build`（项目惯例）。
- [x] **B1.5 E2E 冒烟**：项目详情选模板 → 提交 → 任务页见 build 行 + 状态 badge。

### B 验收

1. 后端零改动 → 既有 1757 测试基线不受影响（实测 `tests/services/kag/` 全绿）。
2. `check:fast` exit 0；前端 build exit 0（2026-09-19 实测）。
3. E2E（2026-09-19 完成，浏览器实测）：选「结构化数据（Git 仓库）」模板 →
   命令自动填充 `kag builder --project_id 3 --git_url <data-repo-url> --commit_id
   <commit-id>` → 提交受理 → 任务页 build 行「失败」badge + 展开可观测
   （复用 P0a，本实例 executor 缺失预期失败可见）。
4. 权限：提交走既有 build 端点（same-origin + membership），无新攻击面。

## 5. 阶段 C — 后续增强（MVP 边界 / 产品决策项，不默认投入）

| 项 | 性质 | 前置条件 | 状态 |
| --- | --- | --- | --- |
| Schema 概念建模（规则/概念树） | MVP 边界 | 产品确认 | ✅ C2（见 5.2） |
| App 编排 | MVP 边界 | 产品决策 | ⏳ 待产品决策，不默认做 |
| 多租户完整授权 | MVP 边界 T3 | 产品排期 | ⏳ T3 排期 |
| 统计/反馈/教程 | 应补齐（低优） | 随手活 | ✅ D1 统计（见 5.3）；反馈未做（无独立需求） |
| 图浏览增强（实体详情/一跳图） | 设计既定方案内 | 见 5.1 | ✅ C1（见 5.1） |

### 5.1 图浏览增强（2026-09-20 拆解，已评审）

范围（用户决策）：**联动详情 + 一跳图（含未实测 DSL）**——节点点击 →
详情面板（当前结果行联动，零新 DSL）+ 「展开一跳」按钮（构造 1-hop DSL）。

前置核实（2026-09-20 完成）：**本环境所有 LOCAL 项目 Neo4j 图库均不存在**
（`Unable to get a routing table for database ... does not exist`）——点查/一跳
DSL 无法用真实图数据实测；DSL 按 M3.3 已验证契约形态构造（节点类型 namespace
全名），**数据路径标注「需图库环境实测」**，UI 错误路径走既有优雅降级。

拆解：
- [x] **C1.1 模型层纯函数** `web/features/kag/model.ts`：
  - `parseDslAliasTypes(dsl)`：解析 `MATCH (n:ns.Type)` → `{n: ns.Type}`；
  - `parseDslRelationLabel(dsl)`：首个 `[p:rel]` 关系 label（裸名）或空；
  - `columnAlias(header)`：`n.id` → `n`（无点/未知返回空）；
  - `buildNodeTypes(dsl, header, rows)`：节点 id → {alias, type}（首次出现
    胜出，缺类型为 null）；
  - `oneHopDsl(type, rel, id)`：`MATCH (n:{type})-[p:{rel}]->(o) WHERE
    n.id = '{id}' RETURN n.id, o.id`（rel 空则 `-[p]->`）。
- [x] **C1.2 画布节点点击**：`CytoscapeMount` 加 `tap` 处理器（`cy.on('tap',
  'node', ...)`），回调父组件（nodeId + type）。
- [x] **C1.3 详情面板**：点击节点 → 面板显示 id/type + 当前结果中匹配行
  （列值等于 nodeId，零新 DSL）；「展开一跳」按钮 → `oneHopDsl` 生成 DSL
  （回填输入框并直接执行，复用 `queryKagGraph`），结果复用现有图渲染。
  类型缺失时提示「无法从 DSL 解析类型」。
- [x] **C1.4 i18n**：详情/一跳相关 key（zh/en，7 个）。
- [x] **C1.5 测试与构建**：`kag-model.test.ts` 覆盖 C1.1 全部纯函数（含
  缺类型/无关系/多别名降级）；`check:fast` exit 0 + 完整 build exit 0。
- [x] **C1.6 验证边界（如实记录）**：本环境无图库 → 交互 E2E（节点点击
  出现详情/一跳图）不可演示；以单测 + typecheck + build 为质量门，浏览器
  实测 DSL 错误路径优雅降级（"routing table ... does not exist" 正常展示、
  页面不崩；提示文案仅在无数据时不出现在画布——符合预期）。
  **2026-09-20 环境更新**：OrbStack 栈已确认齐备（server/mysql/minio/neo4j
  全 Up），缺失的项目库 `m0probelive`/`m2reviewproj` 已补建（`CREATE DATABASE
  IF NOT EXISTS`，Neo4j 多库可用），图查询已恢复 `FINISH`。
  **C1 E2E 已完成（2026-09-20）**：经公开 `write_graph`（
  `/public/v1/graph/writerGraph`）注入 `m0ProbeLive.Person`→`workFor`→
  `Organization` 小子图（person-1/org-1），浏览器实测全链路：关系查询渲染
  2 节点 1 边 → 节点点击 → 详情面板（节点类型 + 匹配行）→ 「展开一跳」
  生成 1-hop DSL（`MATCH (n:Person)-[p:workFor]->(o) WHERE n.id='person-1'
  RETURN n.id,o.id`）→ 返回 person-1→org-1 → 详情面板随新查询清除。
  图标签区同步出现 3 个 label。构建闭环（P0b）仍缺 computing engine driver。

验收：C1.1–C1.4 实现；C1.5 全绿（`test:node` kag-model 685 pass、`check:fast`
exit 0、build exit 0）；C1.6 边界如实记录；无后端改动。

### 5.2 概念建模：概念规则浏览 + 编辑（2026-09-20 拆解，已评审）

范围（用户决策）：CONCEPT_TYPE 展开区新增「概念规则」面板——**分类规则
（belongTo）+ 推理规则（leadTo）浏览与增删**。属 MVP 边界内（概念是 SPG
schema 一等公民；规则是概念建模的核心内容）。

前置核实（2026-09-20 完成：curl 实测运行 server :8887 + openspgapp 源码）：

- **端点**：`ConceptController` 挂 `/public/v1/concept`（knext 生成的
  `/concept/...` 内部路径与 server 不一致——**以运行 server 为权威**），
  读+写共 6 个，响应均为**裸对象/裸 bool**（`HttpBizTemplate.execute`，
  无 `{result:}` 信封）：
  | 方法/路径 | 请求 | 响应 |
  | --- | --- | --- |
  | `GET /concept/getReasoningConcept?name=<type>` | query | `[TripleSemantic]` |
  | `GET /concept/queryConcept?conceptTypeName=<type>[&conceptName=]` | query（conceptName 可省） | `{concepts:[Concept]}` |
  | `POST /concept/defineDynamicTaxonomy` | `{conceptTypeName, conceptName, dsl}` | `true` |
  | `POST /concept/removeDynamicTaxonomy` | `{objectConceptTypeName, objectConceptName}` | `true` |
  | `POST /concept/defineLogicalCausation` | `{subjectConceptTypeName, subjectConceptName, predicateName, objectConceptTypeName, objectConceptName, semanticType, dsl}` | `true` |
  | `POST /concept/removeLogicalCausation` | 同上（无 dsl） | `true` |
- **响应模型（实测 wire）**：`TripleSemantic` = `subjectTypeIdentifier`
  (`{@type:SPG_TYPE, namespace, nameEn}`) / `subjectIdentifier` (`{@type:CONCEPT,
  id, name}`) / `predicateIdentifier` (`{name}`) / object 同 subject +
  `logicalRule{code{code}, version, isMaster, status, content=DSL, creator}` +
  `ontologyEnum`；`Concept` = `name`(CONCEPT) + `semantics[]`（含
  `DynamicTaxonomySemantic`: `conceptTypeIdentifier/conceptIdentifier/
  predicateIdentifier/logicalRule`）。`@type`/`identityType` 为 Jackson 多态
  标记，解析忽略。
- **归属**：`getReasoningConcept` 按 objectTypeNames 过滤（返回 object 概念
  类型=该 type 的规则）；`queryConcept` 返回该类型概念及其全部语义（belongTo
  分类规则 + leadTo 推理规则）。
- **前置条件（实测）**：`defineDynamicTaxonomy` 依赖 schema 中存在 `belongTo`
  属性（实体类型→该概念类型，`ConceptSemanticServiceImpl.queryUniqueIdByPO`
  返回 null 时 server 500 NPE）。**定义表单据此门禁**（`belong_to_ready`），
  不满足时禁用并提示——不盲提交上游 500。
- **演示数据（m0ProbeLive，已注入）**：`Topic` 概念类型（knext 创建，
  taxonomicType=Person；`belongTo` 属性手动补齐——`BuiltInPropertyHandler`
  仅在类型 `isCreate()` 时自动补，knext 重建路径不触发，显式加一次）+
  `Topic/1` belongTo 规则 + `Topic/1→Topic/2` leadTo 规则（`define`/`remove`
  全链路 curl 200 实测）。

拆解：

- [x] **C2.1 `openspg_client.py` 新增 6 个方法**（裸响应直取，无信封）：
  `get_reasoning_concepts(type_name)`、`get_concept_detail(type_name,
  concept_name="")`、`define_dynamic_taxonomy(...)`、`remove_dynamic_taxonomy(...)`、
  `define_logical_causation(...)`、`remove_logical_causation(...)`；沿用
  `_request` 模式 + `_parse_response` 兜底。
- [x] **C2.2 `kag.py` 新路由**（全部 `_require_project_access` + 写操作
  `_require_same_origin`，响应过 `_sanitize`，上游错误 `_upstream_error`）：
  - `GET /projects/{project_id}/concept/rules?type_name=` — 归一
    `{type_name, reasoning[], taxonomy[], belong_to_ready}`：
    `reasoning`=getReasoningConcept（TripleSemantic 归一），
    `taxonomy`=queryConcept（concepts[].semantics 中 DynamicTaxonomySemantic
    归一），`belong_to_ready`=schema 中存在 `belongTo` 属性且 objectTypeRef
    等于该概念类型（读失败降级 false）。reasoning/taxonomy 各自独立容错
    （上游失败降级空列表，不 500）。
  - `POST /projects/{project_id}/concept/rules/define` — body `{kind:
    "logical"|"taxonomy", ...}`；taxonomy 走 defineDynamicTaxonomy（先经
    `_belong_to_ready` 门禁，不满足 400 拒提），logical 走
    defineLogicalCausation（`semanticType` 恒 `REASONING_CONCEPT`）。
  - `POST /projects/{project_id}/concept/rules/remove` — body 同构，taxonomy
    走 removeDynamicTaxonomy（字段名 `objectConceptTypeName`），logical 走
    removeLogicalCausation。
  - Pydantic 模型 + 必填校验（`kind`/`concept_type_name`/`dsl` 等）。
- [x] **C2.3 前端** `web/features/kag/`：
  - `model.ts`：`KagConceptRule`（归一 kind/taxonomy/logical 行：subject/object
    type+name、predicate、conceptName、dsl）+ `parseConceptRules`（宽松归一、
    忽略 `@type`/`identityType`，缺字段安全缺省）。
  - `api.ts`：`fetchKagConceptRules`、`defineKagConceptRule`、
    `removeKagConceptRule`（`apiUrl` 相对路径、`scope:"kag"`）。
  - 新组件 `ConceptRulePanel.tsx`：CONCEPT_TYPE 展开区渲染——规则列表
    （分类/推理分节，DSL 等宽只读、截断）+ 删除按钮 + 定义表单（分类：
    概念名+DSL+`belong_to_ready` 门禁提示；推理：主谓宾概念+谓词+DSL）。
    `KagProjectDetailPage.tsx` 的 `TypeNodeRow` 在 `node.kind==="concept"`
    时与 `SchemaEditPanel` 并列渲染。
  - i18n：新增 26 个 key（英文原文即键，zh/en 双语）。
- [x] **C2.4 测试与契约**：新增 `tests/services/kag/test_concept_rules.py`
  （client 6 方法 × MockTransport、归一 helper、路由浏览/门禁/增删/权限/
  降级/跨站 403，14 项全绿）；前端 `kag-model.test.ts` +2 项
  `parseConceptRules`；`check:fast` exit 0 + 完整 build exit 0 + 重新生成
  `openapi.json` 与前端 TS 契约；后端全量 1700 passed / 6 skipped。
- [x] **C2.5 E2E（2026-09-20 浏览器实测，已完成）**：m0ProbeLive 规则数据
  已注入（Topic 概念类型 + Topic/1 belongTo 规则 + Topic/1→Topic/2 leadTo
  规则）。浏览器实测全链路：展开 Topic → 概念规则面板渲染 2 条规则（分节
  标题/DSL 等宽/删除按钮/定义表单）→ 定义分类规则 Topic/2 → 列表出现 →
  删除 → 消失（提示「规则已删除」）→ 切换推理表单定义 Topic/3→Topic/4 →
  出现 → 删除 → 消失；原始演示数据保持原样。权限门禁由后端测试覆盖
  （非 admin 403 + 跨站 403 + belongTo 门禁 400）。

### 5.3 统计薄件：项目构建统计卡片（2026-09-20 完成）

范围（计划 §5「统计/反馈/教程」的低优薄件）：项目详情页新增「构建统计」
卡片——已受理/执行中/成功/失败计数，**复用既有 `/projects/{id}/builds`**
（P0a live_status 聚合），零新上游契约、零后端改动。

实现（`KagProjectDetailPage.tsx`）：
- 主加载 effect 内并行 `fetchKagBuilds`（失败降级 `buildStats=null`，不阻塞
  详情）；`StatChip` 渲染 4 个计数 chip；i18n 新增 2 个 key
  （"Build statistics"/"Accepted"，zh/en）。
- 质量门：`check:fast` exit 0、完整 build exit 0（2026-09-20 实测）。
- E2E（2026-09-20 浏览器实测）：项目 3 详情页渲染「构建统计 已受理 3 ·
  执行中 0 · 成功 0 · 失败 3」——与本地 3 个失败 build 记录一致（executor
  缺失预期）。

「反馈」未做（管理面无独立反馈需求）；「教程」文案已随各面板（图浏览/导入/
构建/概念规则）具备，不新增冗余说明。

## 6. 风险与待确认

1. **已实测（A1.4）**：`BuilderJob.status` 恒 `RUNNING`，不随执行更新；
   成败判定取节点级（`taskDag.nodes`/`SchedulerTask.status`）聚合，实例级
   status 仅兜底。A1.4 原待确认项已关闭。
2. `/public/v1/scheduler/*` 与 `/public/v1/builder/*` 沿用 `/public` 无认证、
   信任调用方的既有假设（设计 §3.2/§6.1），内网部署前提不变。
3. 实时状态来自 OpenSPG server 存储，openkg-webui `task_store` 仍是 advisory 摘要
   （滚动 500 条、不跨进程锁），正确性以 server 为准，UI 标注来源。

## 7. 评审记录

2026-09-19 方案评审（证据：openspgapp 源码 + openkg-webui 既有实现核实）：

- **P1-1（数据源缺口）**：`/kag/tasks` 列表只读本地 `task_store`，原方案把
  live 合并只放在项目级 builds 端点——任务页拿不到实时状态。修正：扩展
  `GET /tasks` 对 build 记录做并行（`asyncio.gather`、逐条容错）live 合并。
- **P1-2（响应信封，已核实）**：builder/scheduler 控制器走
  `HttpBizTemplate.execute2` → `{result: ...}` 信封；project/reason 走
  `execute` → 裸对象。client 方法须统一解包 `result`（search 解到 `results`）。
- **P2-3（提交路径简化）**：去掉提交后回查 taskId——详情端点惰性经
  `get_builder_job` 解析，`scheduler_job_id` 仅作可选缓存。
- **P2-4（验收补强）**：OpenSPG 不可达时断言 inference 行不受影响、不 500。
- **P3-5（契约导出）**：非全量快照；新端点需过 operation_id 唯一性断言并
  重新生成已提交 `openapi.json`/前端 TS 类型。
- **P3-6（轮询）**：手动刷新为主，10s 轮询可选（最小改动）。
- **风险降级**：失败可见性不依赖 `BuilderJob.status` 更新（SchedulerInstance
  status 必然推进），A1.4 仅决定展示口径。

2026-09-20 C2 代码评审（实施后；质量 + 安全）：

- **P2-7（静默默认）**：logical 定义路由对空主语概念名静默默认 `"1"`，与
  remove 路径（必填 400）语义不一致，且会掩盖客户端缺陷。修正：主语概念名
  必填（400），补单测（`test_define_logical_requires_subject_concept_name`）。
- **P2-8（竞态）**：`ConceptRulePanel.load()` 无取消守卫——快速切换概念类型
  时慢响应可能覆盖新类型规则。修正：共享 `useRef` 取消守卫（卸载/切键时丢弃
  过期响应），与项目既有 `cancelled` 模式一致。
- **确认无问题**：same-origin + membership 门禁、DSL 文本渲染无 XSS（React
  转义）、JSON body 无 shell 注入、`_sanitize` 掩码、operation_id 唯一性、
  裸响应无信封、`belong_to_ready` 门禁、独立容错降级。

## 8. P0b 构建闭环实施排期（独立工作流，2026-09-20 排期）

状态：**已排期**（不随 openkg-webui 阶段 A–D 交付；独立仓库 openspgapp + 独立
构建链）。范围：自研**本地 computing engine driver**（子进程跑 `kag builder`
并轮询），打通「提交构建 → 真正执行成功 → 图数据落库 → 状态推进」闭环。

### 8.1 前置核实（2026-09-20 深入源码，补充 §2.3 结论）

- **接口链（已确认）**：`ComputingEngineAsyncTask`（
  `core/scheduler/service/task/async/builder/`）→
  `ComputingEngineClientDriverManager.getClient(value.getComputingEngineUrl())`
  → 按 URL scheme 匹配 `driver.acceptsConfig(scheme)` → `driver.connect(url)`。
  接口：`ComputingEngineClient<T>{ submitBuilderJob(BuilderJob, T extension),
  queryStatus(T, String id), stop(T, String id) }`（T=extension JSONObject）。
- **Driver 注册双通道**：`DriverManagerUtils.loadDrivers("cloudext.cache.drivers",
  ComputingEngineClientDriver.class)` = ①Java ServiceLoader
  （`META-INF/services/com...ComputingEngineClientDriver`）②系统属性列出类名
  `Class.forName`；driver 均须**static block 自注册**
  （参考 `TuGraphStoreClientDriver`：`static { XxxDriverManager.registerDriver(
  new XxxDriver()) }`，`driverScheme()` 返回 scheme）。
- **extension 内容（已确认）**：COMMAND（KAG_COMMAND 类型=用户提交的原命令；
  其他类型=服务端拼 `SPG_DEFAULT_COMMAND`）+ PYTHON_EXEC_OPTION（`python.exec`）
  + PYTHON_PATHS_OPTION + SCHEMA_URL + GRAPH_STORE_URL + SEARCH_ENGINE_URL
  + MODEL_EXECUTE_NUM + PROJECT_OPTION（project JSON）。
- **状态机与重试（已确认）**：`ComputingStatusEnum{SUBMIT, RUNNING, SUCCESS,
  FAILED, STOP, NOTFOUND, UNDEFINED}`；FAILED→重试（executeNum%10==0 重提交，
  异常计数>50 终止）；NOTFOUND→每 5 次重提交；RUNNING→instance 置 RUNNING。
- **配置键**：`cloudext.computingengine.url`（Spring @Value，默认空串——空则
  `driverNotExist` 502，即现状根因）。
- **部署链（已确认）**：release server 为**预构建镜像**
  `spg-registry.../openspg-server:latest`（`openspg/dev/release/docker-compose.yml`
  command 传 `--cloudext.*` 参数）；镜像构建链
  `docker/dev/server/buildx-release-server-aliyun.sh`（Dockerfile + maven）。
  executable jar 模块 `openspg/server/arks/sofaboot` → `api-http-server` →
  `core-scheduler-service`（已依赖 `cloudext-impl-objectstorage-minio`）；
  cloudext impl 在父 pom `openspg/pom.xml` dependencyManagement 登记。
- **排除项**：openspgapp fork 的 `cloudext/interfaces/computing`（
  `com.antgroup.openspgapp...`）+ `computing/local` stub + `computing-engine/
  aistudio`——**死代码/未接线**（fork 命名空间，无任何引用），不走这条线；
  新 driver 放**上游命名空间** `openspg/cloudext/impl/computing-engine/local/`。

### 8.2 里程碑拆解

| 里程碑 | 内容 | 依赖 | 验证门 |
| --- | --- | --- | --- |
| **M0 基线** | openspgapp 本地可构建 executable jar + 重建 server 镜像（含新模块接入点）。**改造范围（2026-09-20 源码完整性核实后修正）**：①Dockerfile `git clone 内网`→`COPY 本地源码`；②**排除 fork 私有模块**（`cloudext/impl/computing-engine/aistudio` 依赖 `aistudio-workflow`/`kubemaker-client-java` 私有构件、`cloudext/impl/search-engine/kgfabric`）；③**补 `openspg/dev/release/python/lib/builder*.jar`/`reasoner*.jar` 拷贝**（fork git 无此路径，Dockerfile COPY 会失败——改为从 `builder/runner`、`reasoner/runner` 的 maven 产物拷贝或手工作业）；④依赖源本地化（maven 公共 mirror、miniconda 公共源、kag 本地源码安装） | maven + JDK17 + docker（OrbStack）；磁盘/耗时 | 本地 `docker compose up` server 启动 + 既有项目/图查询回归 |
| **M1 Java driver** | 新模块 `openspg/cloudext/impl/computing-engine/local/`（artifactId `cloudext-impl-computing-engine-local`）：`LocalComputingEngineClientDriver`（scheme=`local`、static 自注册）+ `LocalComputingEngineClient implements ComputingEngineClient<JSONObject>`：submit=ProcessBuilder 以 `python.exec` 起 COMMAND 子进程、taskId=注册表 key、logUrl=日志文件；queryStatus=进程存活→RUNNING / exit0→SUCCESS / 非0→FAILED / 未知→NOTFOUND；stop=kill。注册表内存+磁盘双写（重启可查）。父 pom 登记 + `core-scheduler-service` 依赖 | M0 | driver 编译 + 单测（注册表状态机、进程生命周期） |
| **M2 镜像 + worker 环境** | 重建镜像含新 driver；容器内装 `kag` CLI（镜像已含 pemja python）与 git；挂载/内置 KAG 项目（`kag_config.yaml`：LLM key、vectorizer base_url 指向容器可达地址如 `host.docker.internal`）；compose 增 `--cloudext.computingengine.url=local://exec` + `--python.exec=...` | M1 | 容器内手工跑 `kag builder --git_url <本地仓库路径>` smoke 成功 |
| **M3 端到端** | openkg-webui 提交真实 KAG_COMMAND build → 调度器 → local driver 子进程 → 图写入 Neo4j → RUNNING→SUCCESS → openkg-webui 任务页成功 badge + 图浏览可见新数据；失败命令路径 → FAILED + traceLog | M2 | openkg-webui E2E（P0a 可观测已就绪，零前端改动） |
| **M4 收尾** | driver 代码评审 + openspgapp 独立分支/PR；openkg-webui 侧仅文档；计划归档 | M3 | 评审记录 + 提交 |

### 8.3 风险与决策点

- **镜像构建成本**：sofaboot maven + docker build 耗时/磁盘大——决策：复用
  openspgapp 官方构建链（`docker/dev/server`），跳过跨平台 buildx（本机
  arm64 单平台即可）。
- **worker 网络**：容器内 `kag builder` 需访问 LLM/embedding/Neo4j——graphstore
  经 extension 自动携带（`neo4j://release-openspg-neo4j` 容器内可达）；
  vectorizer 与 LLM 需指向宿主可达地址（`host.docker.internal`），数据仓库
  用本地 git 路径（`git clone <本地路径>` 可行，已核实 KAG builder.py）。
- **重试语义**：本地 driver 对失败命令应尽快返回 FAILED（调度器 10 次后
  ERROR）；NOTFOUND（进程表重启丢失）会触发重提交——注册表持久化到磁盘缓解，
  幂等由 kag builder 自身保证。
- **KAG 版本对齐（2026-09-20 修正）**：构建链存在**三处版本不一致**——server
  Dockerfile runtime 装 `openspg-kag==0.6.0b9`、python 基础镜像装
  `openspg-kag==0.7.0`（server 阶段会覆盖为 0.6.0b9）、本地 [KAG 仓库
  `~/project/KAG`](../../../KAG) 源码为 **0.8.0**。**决策：M2 worker 环境
  改用本地 KAG 源码安装**（容器内 `pip install -e /Users/simon/project/KAG`
  或本地构建产物），绕开内网 pip 源（`artifacts.antgroup-inc.cn`）且与
  openkg-webui 演示项目（m0ProbeLive）/B-1 导入模板（基于本地 KAG builder CLI
  语法）对齐；兼容点（0.8.0 `kag builder` 与 server extension 契约）在 M2
  smoke 实测。
- **Miniconda 安装包仅内网**：python 基础镜像 Dockerfile 从
  `hyperloop.cn-hangzhou.alipay.aliyun-inc.com/.../Miniconda3-py310_...sh`
  下载（无源码、仅内网）——M2 重建时换公共 miniconda 源。
- **`release/python/lib` jar 缺失**（见 §8.5 D1）：M0.3 前需补齐，否则
  Dockerfile `COPY --from=BUILDER` 阶段失败。

### 8.5 构建链源码完整性分析（2026-09-20 核实）

结论先行：**openspg 业务 Java 源码与两个基础镜像的构建链源码均在本地；
真正的"无源码"分三类——仅内网的外部二进制/构件、fork 缺失的发布物、
以及（刻意留白待 M1 补的）computing-engine 实现。**

**① 有源码（本地）**

| 部件 | 位置 |
| --- | --- |
| openspg Java 全模块（common/server/reasoner/builder/cloudext） | `openspgapp/openspg/`（与 `openspg/pom.xml` modules 一致） |
| fork 模块（arks/api/biz/core/infra/cloudext computing stub） | `openspgapp/` 根 pom modules（stub 文件在） |
| openspg-base 镜像构建链 | `openspgapp/docker/base/Dockerfile`（ubuntu + openjdk-8 + maven，**纯公共源可重建**） |
| openspg-python 镜像构建链 | `openspgapp/openspg/dev/release/python/Dockerfile` |
| knext / kag python 源码 | `~/project/KAG`（**0.8.0**，独立仓库） |

**② 无源码、但公共源可获取（正常依赖）**

- maven 公共构件（spring/fastjson/jackson/neo4j-driver/scala/cats…）；
- apt 包（openjdk-8/git/maven）、pip 公共包（pemja==0.4.0）、miniconda（公共下载源）。

**③ 无源码、仅内网可达（fork 构建断点，需本地化/排除）**

| 断点 | 内容 | 影响 |
| --- | --- | --- |
| **D1 `release/python/lib/builder*.jar`/`reasoner*.jar`** | fork git **无此路径**（`git ls-files` 为空）；Dockerfile `COPY --from=BUILDER ...` 必失败；源码对应模块 `openspg/builder/runner`、`openspg/reasoner/runner` **在本地** | M0.3 前从模块 maven 产物补齐拷贝/手工作业 |
| **D2 私有 maven 构件** | `aistudio-workflow`、`kubemaker-client-java`（aistudio 模块）；仅 `artifacts.alipay.com` | 构建排除 aistudio 模块 |
| **D3 内网 maven/pip 源** | `artifacts.alipay.com` / `artifacts.antgroup-inc.cn`（Dockerfile 内） | 换公共 mirror；上游主链依赖多为公共构件 |
| **D4 miniconda 安装包** | python 镜像 `wget` 自 `hyperloop.cn-hangzhou.alipay.aliyun-inc.com` | 换公共 miniconda 源 |
| **D5 openspg-kag pip 包** | 外部包（0.6.0b9/0.7.0 不一致）；**源码本地有（KAG 0.8.0）** | M2 改用本地 KAG 源码安装 |

**④ 完全缺失（本地无此物）**

- 上游 `openspg/cloudext/impl/computing-engine/` 实现模块（interface 有、
  impl 无）——**P0b 的 M1 就是补这个**（非缺失，属上游未提供）；
- 运行时配置（`kag_config.yaml`：LLM key、vectorizer、项目目录）——需自备，
  非源码；
- 预构建运行时镜像 `openspg-server:latest`——M0 的目标就是重建它（本地
  fork 无对应 Dockerfile 产出物，需走 M0.2 改造后的构建链）。

### 8.6 computing-engine 实现详细设计（M1，2026-09-20）

#### 8.6.1 作用（构建闭环中的角色）

```
openkg-webui submit_build (POST /projects/{id}/build)
  → OpenSPG /public/v1/builder/kag/submit（受理，建 BuilderJob）
  → 调度器 SchedulerJob/Instance/Task（建 DAG：Builder + PostProcessor）
  → ComputingEngineAsyncTask.submit()
      = ComputingEngineClientDriverManager.getClient(cloudext.computingengine.url)
        .submitBuilderJob(builderJob, extension)   ← 本次要补的实现
  → 返回 ComputingTask{taskId, logUrl} → 存 SchedulerTask.resource
  → 调度轮询 ComputingEngineAsyncTask.getStatus() → client.queryStatus(ext, taskId)
  → 状态推进 RUNNING→SUCCESS → instance FINISH（进度 100%）
  → PostProcessor（kagCommandPostSyncTask）→ openkg-webui P0a 状态可观测展示
```

接口职责（`ComputingEngineClient<JSONObject>`，3 方法）：
- `submitBuilderJob(builderJob, extension)`：把构建命令变成**可跟踪的异步执行**并登记，返回 `taskId` + `logUrl`；
- `queryStatus(extension, taskId)`：进程/任务状态 → `ComputingStatusEnum{RUNNING/SUCCESS/FAILED/STOP/NOTFOUND}`；
- `stop(extension, taskId)`：终止任务 → Boolean。

关键约束：必须与调度器重试/终止语义兼容（FAILED 每 10 次 executeNum 重提交、
异常计数 >50 终止、NOTFOUND 每 5 次重提交）。

#### 8.6.2 执行命令形态（已核实）

- **KAG_COMMAND 类型（openkg-webui 主路径）**：`extension.COMMAND` = 用户提交的
  原命令（如 `kag builder --project_id 3 --git_url <repo> --commit_id <id>`），
  `initExtension` 原样保留（`BuilderConstant.COMMAND`）。**本地 driver 直接
  执行该命令串**——无内网依赖。
- **非 KAG_COMMAND 类型**：`initExtension` 拼 `SPG_DEFAULT_COMMAND`
  （`pip install openspg-kag-ant==0.8.0... -i https://artifacts.antgroup-inc.cn/... &&
  python -c '...kag.bridge.spg_server_bridge.SPGServerBridge.run_builder(...)'`）——
  **依赖内网 pip 源与 `kag_ant` 包**（ant 内部变体，本地 KAG 0.8.0 不含）。
  **M1 范围：仅支持 KAG_COMMAND；其他类型 submit 即登记"即时失败"任务，
  queryStatus 返回 FAILED 并在 logUrl 写明原因**（openkg-webui 只提交 KAG_COMMAND，
  不影响演示闭环）。
- extension 附带（`BuilderConstants`）：`pythonExec`/`pythonPaths`（python.exec
  配置）、`schemaUrl`、`graphStoreUrl`、`searchEngineUrl`、`modelExecuteNum`、
  `project`(project JSON)——driver 可将 `pythonExec` 用作子进程解释器、
  将 graphStoreUrl 等以环境变量注入（供 kag builder 复用）。

#### 8.6.3 实现设计

模块：`openspg/cloudext/impl/computing-engine/local/`
（artifactId `cloudext-impl-computing-engine-local`，**Java 8**；父 pom
dependencyManagement 登记 + `core-scheduler-service` 依赖）。

```
LocalComputingEngineClientDriver extends CachedCloudExtClientDriver<ComputingEngineClient>
    implements ComputingEngineClientDriver
  driverScheme() = "local"
  static { ComputingEngineClientDriverManager.registerDriver(new LocalComputingEngineClientDriver()); }
  innerConnect(url) -> new LocalComputingEngineClient(url)

LocalComputingEngineClient implements ComputingEngineClient<JSONObject>
  字段：connUrl（含 query 参数：workdir/python/timeoutSec 等配置）
        注册表 ConcurrentHashMap<String, LocalTask> + 磁盘持久化（重启加载，缓解 NOTFOUND）
  LocalTask{ id, pid, exitCode(volatile), startTime, logFile, done }
```

- **submitBuilderJob**：
  1. 取 `extension.command`（仅 KAG_COMMAND 支持，否则登记即时失败任务）；
  2. 生成 `taskId = "local-" + ts + "-" + seq`；`logFile = <workdir>/logs/<taskId>.log`；
  3. `ProcessBuilder("/bin/sh", "-c", command)`（完整命令串语义），
     环境注入 `SPG_GRAPH_STORE_URL`/`SPG_SEARCH_ENGINE_URL`/`SPG_SCHEMA_URL`
     （来自 extension，供 kag builder 使用），工作目录 = `workdir`；
     stdout/stderr 合并追加写 logFile；
  4. 启动成功 → 登记 LocalTask（内存+磁盘）→ 返回
     `ComputingTask{taskId, logUrl=logFile}`；
     启动异常（命令不可执行等）→ 登记 done=true、exitCode≠0 → 仍返回 taskId，
     queryStatus 返回 FAILED（**submit 阶段不抛，交由状态机表达失败**）。
- **queryStatus**：查注册表——不存在 → `NOTFOUND`；进程存活
  （`Process.isAlive()`，以 pid 兜底 `kill -0`）→ `RUNNING`；已退出 →
  exitCode==0 ? `SUCCESS` : `FAILED`；超过 `timeoutSec`（默认 3600）→ `STOP`。
- **stop**：查 pid → `destroy()`（超时再 `destroyForcibly`）→ true。
- **幂等（防重提交冲突）**：调度器 FAILED/NOTFOUND 会重调 submit——同
  BuilderJob.id 已存在未结束任务时直接返回既有 taskId，避免重复起进程。
- **并发**：注册表加锁（调度轮询与提交可能并发；server 单 worker 亦保障语义）。

#### 8.6.4 配置

compose server command 增加：
`--cloudext.computingengine.url=local://exec?workdir=/data/worker`、
`--python.exec=<worker venv python>`、`--python.paths=<kag 包路径>`。
worker 目录 `/data/worker` 挂载卷：KAG 项目（`kag_config.yaml`）、数据仓库、
日志目录。

#### 8.6.5 worker 环境依赖（M2）

- **kag 0.8.0**：容器内 `pip install -e <本地 KAG 源码>`（本地化决策，§8.3）；
  `kag builder` 主链**纯 python**（已核实 `LOCAL_REASONER_JAR` 常量无消费者，
  本地 KAG 0.8.0 构建不依赖 Java reasoner jar——D1 jar 对 openkg-webui 主路径非必需，
  M2 smoke 保留验证点）。
- 容器内已有：java 8（openspg-python 基础镜像含）、pemja、git、venv python。
- KAG 项目：`kag_config.yaml`（LLM key、vectorizer base_url 指向
  `host.docker.internal`）；数据仓库用本地 git 路径（`git clone <本地路径>`
  可行，已核实 KAG builder.py）。

#### 8.6.6 验证路径

- **M1 单测**：注册表状态机（submit→RUNNING→SUCCESS / FAILED / NOTFOUND /
  STOP / 幂等重提交）；真子进程命令（`echo ok`→SUCCESS、`exit 1`→FAILED、
  未知 taskId→NOTFOUND）。
- **M2 smoke**：容器内手工 `kag builder --project_id 3 --git_url <本地仓库>`
  成功（图数据写 Neo4j）。
- **M3 E2E**：openkg-webui 提交真实 KAG_COMMAND → local driver 子进程 → RUNNING→
  SUCCESS → openkg-webui 任务页成功 badge + 图浏览可见新数据；错误命令 → FAILED +
  traceLog 可见。

#### 8.6.7 风险

- 调度器重试语义 × 本地进程：幂等 submit 防重复起进程；失败命令尽快 FAILED
  （避免拖满 10 次重试）。
- 长任务超时：`timeoutSec` 默认 3600s，超时 STOP（调度器按 STOP=FAILED 处理）。
- 进程泄漏：server 重启后孤儿进程——注册表磁盘持久化 + 启动时清理孤儿。
- `kag 0.8.0` builder 与 server extension 契约（`pythonExec` 是否必须、命令
  工作目录语义）——M2 smoke 实测校准。

### 8.7 调度器功能与实现机制分析（2026-09-20 源码核实）

范围：`openspg/server/core/scheduler/`（model/service）+ 相关提交入口。调度器
= 任务全生命周期编排：**受理 → DAG 翻译 → 实例生成（定时）→ 执行（DAG 状态机）
→ 重试/终止 → 完成回调**。两大 60s 定时循环 + 两级线程池 + mysql 持久化 +
两级分布式锁。

#### 8.7.1 提交入口（BuilderController.kag/submit）

1. 建 `BuilderJob`：type=`KAG_COMMAND`、lifeCycle=`ONCE`、computingConf=request
   JSON、status=`RUNNING` → insert；
2. `createSchedulerJob`：`SchedulerJob{translateType=KAG_COMMAND_BUILDER,
   invokerId=builderJob.id, dependence=INDEPENDENT, status=ENABLE,
   lifeCycle=ONCE}` → `schedulerService.submitJob(job)`（ONCE job 提交时即生成
   首个 instance）；
3. 回写 `builderJob.taskId = schedulerJob.id`（P0a 详情链路据此关联）。

#### 8.7.2 DAG 翻译（Translate）

- `Translate.translate(SchedulerJob) → TaskExecuteDag`；
  `statusCallback(job, instance, instanceStatus)` 在 instance 终态回写
  `BuilderJob.status`（A1.4 实测恒 RUNNING 的疑点：该回调未稳定触发——
  故 P0a 以节点级状态为权威，正确）。
- `KagCommandBuilderTranslate`（translateType 按 Spring Bean 名查找）：
  KAG_COMMAND 的 DAG = `Builder(computingEngineAsyncTask)` →
  `PostProcessor(kagCommandPostSyncTask)`；节点 `taskComponent` 即 **Spring
  Bean 名**，运行时按名取 `TaskExecute`。

#### 8.7.3 调度驱动（SchedulerHandlerClient，`scheduler.handler.type=db`）

- 启动时把容器内所有 `SchedulerHandler` bean 注册进 DB（`SchedulerInfo` 表），
  每个 handler 一个 `ScheduledThreadPoolExecutor`（core=1、daemon）按 period
  定时执行 `process()`。
- **分布式锁**：`SchedulerInfo.lockTime` 抢占，10min 超时自动释放；IP 白名单
  过滤——多实例防重复调度。
- **节流**：RUNNING 且距上次执行 >`hostExceptionTimeout`(300s) 才重跑；WAIT
  且超过 period 才跑。
- 内置 handler（均 60s）：
  - `generateInstanceScheduleHandler` → `generateInstances()`（PERIOD+ENABLE
    job 按周期生成 instance）；
  - `executeInstanceScheduleHandler` → `executeInstances()`（执行所有未完成
    instance）。

#### 8.7.4 核心引擎（SchedulerExecuteServiceImpl）

- `executeInstances()`：查近 `executeMaxDay+1` 天内所有未完成 instance → 按
  `instance.type` **分池**（每 type 一个 ThreadPoolExecutor 20-100 线程、
  队列 10 万）→ `executeInstance(id)`。
- `executeInstance(id)`（每轮状态机）：
  1. 取 instance + tasks；2. 无 RUNNING 任务 → `checkAndUpdateWaitStatus`
     （DAG 前置全 FINISH 的 WAIT 任务 → RUNNING）；3. 无任务可执行 →
     `setInstanceFinish(FINISH)`；4. 否则 `executeTask` 逐个执行。
- `executeTask`：`task.type.split("_")[0]`（如 `computingEngineAsyncTask`）→
  Spring bean(`TaskExecute`) → `executeEntry(context)` → 任务完成后
  `executeNextTask`（**10s 延迟调度下一级节点**——DAG 级联推进，事件驱动）。
- **两级线程池**：实例池（20-100/type）+ 级联调度池（ScheduledThreadPool
  Executor(10)，10s 延迟）。

#### 8.7.5 任务执行模板（TaskExecuteTemplate / AsyncTaskExecuteTemplate）

- `executeEntry`（final 模板方法）：`lockTask`（任务级锁，10min 超时自动重试）
  → `before` → `execute` → `processStatus`。
- 状态处理：`execute` 返回 FINISH → `setTaskFinish`：更新完成时间；末节点且
  全部完成 → `setInstanceFinished`；否则 `startNextNode`（前置全完成 →
  下一节点 WAIT→RUNNING）。
- `finallyFunc`：`executeNum+1`、traceLog 合并（remarkLimit 截断）、
  `replace` 落库（**执行次数/日志持久化**——P0a 观测的基础）。
- **异步任务**（computingEngineAsyncTask）：`AsyncTaskExecuteTemplate.execute`：
  resource 空 → `submit`（computing engine 提交，taskId 存 resource）→
  RUNNING；resource 非空 → `getStatus`（轮询 computing engine 状态）。

#### 8.7.6 状态模型与存储

- `TaskStatus{WAIT, RUNNING, FINISH, ERROR, TERMINATE, SET_FINISH}`；
  `isRunning={RUNNING,ERROR}`；`isFinished={FINISH,TERMINATE,SET_FINISH}`。
- `InstanceStatus{WAITING, RUNNING, FINISH, TERMINATE, SET_FINISH}`；
  `LifeCycle{PERIOD, ONCE, REAL_TIME}`；`Status{ENABLE, DISABLE}`。
- 元数据存 mysql（`scheduler_job/instance/task/info` 表）——P0a 的
  `/public/v1/scheduler/*` 即查这些表。

#### 8.7.7 同步任务池（MemoryTaskServer）

- 线程池（3-20、队列 5000、拒绝时阻塞入队）+ `CompletableFuture` 异步执行；
  任务 WAIT→RUNNING→FINISH/ERROR；**执行完回调
  `schedulerService.triggerInstance(instanceId)`**（事件驱动推进）；stopTask=
  `future.cancel(true)`。同步节点（如 kagCommandPostSyncTask）走此池。

#### 8.7.8 与 computing-engine driver 的交互（P0b 关键）

- **submit**：`ComputingEngineAsyncTask.submit` → `getClient(url)
  .submitBuilderJob(builderJob, extension)` → taskId 存 `SchedulerTask.resource`
  （幂等：resource 非空即跳过 submit 直接轮询）。
- **轮询**：每轮 `executeInstances`(60s) → `executeInstance` → `executeTask`
  → `computingEngineAsyncTask.execute` → resource 非空 → `getStatus` →
  `queryStatus` → 状态映射。
- **重试语义（driver 必须兼容，§8.6.1）**：FAILED → `executeNum%10==0`
  重提交（重新 submit）；异常计数 >50 → TERMINATE；NOTFOUND →
  `executeNum%5==0` 重提交。
- **任务锁**：任务级 lockTime 10min > 轮询周期 60s——单任务不会并发重复执行；
  driver 侧幂等 submit 防"重试撞车"（§8.6.3）。

### 8.8 方案评审（2026-09-20，P0b 排期 §8 自评）

总体：方案成立——接口链/契约/部署链/worker 依赖均已源码核实，M0–M4 拆解
有明确验证门。评审发现 4 个实质问题：

- **R1（P1 日志可见性缺口）**：调度器 `addTraceLog` 只记录状态与 driver
  异常；本地 driver 的子进程 stdout/stderr 仅写 logFile（`ComputingTask.
  logUrl`），**不会进 `SchedulerTask.traceLog`** → 构建失败时 openkg-webui 只能
  看到"task failed"类调度日志，看不到 builder 详细输出。**修正**：M3 验收
  增加"失败原因可读"——方案选项：①driver 把 logFile 放挂载卷、openkg-webui 后端
  新路由读取（越权/路径安全成本高）；②**薄层方案（推荐）**：openkg-webui 不读
  server 文件，traceLog 分类展示失败根因 + 文档标注 logUrl 为容器内路径
  （运维可查）。选 ②，P0a 详情"节点 trace_log + 失败分类引导"。
- **R2（P2 超时默认）**：`timeoutSec=3600` 对真实 KAG 构建（LLM 建链常
  >1h）偏小；且超时→STOP→调度器按 FAILED 处理→触发重试。**修正**：默认
  `86400`（24h）且可在 `local://exec?timeoutSec=` 覆盖；文档明确 STOP=FAILED
  语义。
- **R3（P2 进程守护显式化）**：Java `Process.exitValue()` 在未结束时抛
  IllegalThreadStateException。**修正**：submit 后立即起**守护线程**
  `waitFor() → 记录 exitCode → 置 done`；`queryStatus` 只读 `done/exitCode`
  （volatile），不用 exitValue()。
- **R4（P1 安全边界升级）**：openkg-webui 现允许项目成员提交任意命令（
  MemberBuildPanel），P0a 阶段"受理但不落地执行"；本地 driver 落地后**任意
  命令将在 server 容器内真实执行**——命令注入/容器逃逸/凭据暴露面升级。
  **修正**：①driver 子进程默认以**受限非 root 用户**运行；②kag_config 等
  凭据**不注入子进程环境**（只传 graphStore/schema URL 等非密项）；③文档
  声明"本地 driver 仅限可信内网部署"（与 `/public` 无认证同前提）；④命令
  已随 task.question 落库（审计留存）。
- R5（P3 影响面收窄）：非 KAG_COMMAND 的 BuilderJob（含未来 B-2 导入）走
  SPG_DEFAULT_COMMAND（内网 kag_ant）→ 本地 driver 即时失败。openkg-webui 仅暴露
  KAG_COMMAND，故无实际影响；文档标注即可。
- R6（P3 gate 0）：M0.1 可达性探测是**前置 gate**——失败则方案降级（评估
  开源 openspg 纯 central 构建链）。

### 8.9 M5 上游化后配置 executor：功能需求与实现方案（2026-09-20 分析）

**定义**：文档 §2 的"M5 上游化后配置 executor"= 构建执行后端（computing
engine / executor）由上游（KAG/OpenSPG）提供或自研（§8 local driver）后，
**通过配置启用真正的构建执行**。§8 排期即"自研 local://"路径；本节分析
"配置 executor"作为 openkg-webui 管理能力的需求与实现。

#### 8.9.1 功能需求（openkg-webui 管理面视角）

1. **配置归属（决策）**：executor 配置的**权威源在 OpenSPG server 侧**
   （`cloudext.computingengine.url`，compose command / env），不在 openkg-webui
   `data/user/settings`。openkg-webui 保持管理薄层定位，**不新增 executor 配置写
   端点**（避免双配置源；与项目"单一配置源"约束一致）。
2. **状态可见性**：openkg-webui 应能向运维回显 executor 状态（是否配置、scheme
   类型、最近构建成败），用于排查"构建为什么失败"。
3. **失败引导**：构建失败时按根因分类给出可执行提示：
   - executor 未配置（`cannot find driver` / `driver not exist`）→ 提示
     "请先在 OpenSPG server 配置 cloudext.computingengine.url"；
   - 命令/执行失败 → 展示 traceLog + 指向 logUrl（容器内路径，运维可查）；
   - 图库/上游不可达 → 既有 502 降级路径。
4. **文案动态化**：本地 driver 落地后，B-1 导入面板 / MemberBuildPanel 的
   "仅受理、执行依赖远程 executor"提示应按 executor 状态动态化（已配置 →
   不再显示"仅受理"警告）。

#### 8.9.2 实现方案（零新 server 契约）

- **约束**：OpenSPG server **无公开端点**回显 `cloudext.computingengine.url`
  配置 → openkg-webui 无法直接读配置。
- **方案（行为探测 + 错误分类，薄层落地）**：
  - 后端：`GET /projects/{id}/builds` 与 `GET /builds/{id}` 的响应增加
    `failure_reason` 分类字段——后端对 P0a 已采到的节点 traceLog/状态做
    关键词分类：`cannot find driver`/`driverNotExist` → `executor_unconfigured`；
    `ERROR`/非 0 退出 → `command_failed`；其余 → `upstream_error`。零新
    上游调用。
  - 前端：构建详情/任务页按 `failure_reason` 渲染引导文案（i18n 3–4 个
    key）；B-1/MemberBuildPanel 提示文案在"最近构建成功过"或
    `executor_unconfigured` 不再出现时收敛为常态说明。
- **M5 上游化的真正贡献**：上游提供官方 driver/executor 后，openkg-webui 无需
  改动（配置在 server）；仅 `failure_reason` 词表随 driver 形态补充（如
  `aistudio://` 的鉴权失败词）。
- **验收**：`failure_reason` 分类单测（MockTransport 样本）+ 前端文案渲染；
  本环境现状（executor 未配置）E2E 应显示"executor 未配置"引导。

**实现记录（2026-09-20 完成）**：
- 后端：`kag.py` 新增 `_classify_failure(nodes, live_status)`（traceLog 关键词
  `cannot find driver`/`driver not exist` → `executor_unconfigured`；其余
  failed → `command_failed`；非失败 → null），`GET /builds/{id}` 响应增加
  `failure_reason`。单测 +6（分类 + 详情路由断言）。
- 前端：`model.ts` 的 `KagBuildDetail.failureReason` 解析（未知值→null）；
  `KagTasksPage` 详情区按分类渲染引导文案（executor 未配置=amber 提示 /
  命令失败=red 提示）；i18n +2 key。前端单测 +1。
- 质量门：后端 78 passed、`check:fast` exit 0、完整 build exit 0。
- E2E（2026-09-20 浏览器实测）：任务页展开 `echo p0a-smoke` 失败 build →
  渲染「构建 executor 未配置。请先在 OpenSPG server 配置
  cloudext.computingengine.url。」+ 节点 traceLog（cannot find driver）。
- 未做：列表页 `failure_reason`（无 traceLog 数据，避免误导）；B-1/构建面板
  文案动态化（依赖"最近成功"状态，待本地 driver 落地后随 §8.9.2 一并做）。

**评审记录（2026-09-20，实施后；质量 + 安全）**：

- **确认无问题**：关键词匹配小写归一（大小写不敏感）；非失败状态返回 null
  不产生误导；未知 `failure_reason` 值前端归一为 null；分类在详情端点
  （有节点 traceLog）而非列表（无数据，避免全标 command_failed——与 §8.9.2
  原文"列表+详情都加"做了收敛，收敛合理）；词表扩展路径已定（随 driver
  形态补充，§8.9.2）。
- **边界 R-a（P3，记录）**：详情端点 `taskDag` 兜底路径（`search_scheduler_
  tasks` 为空/失败时）节点 `trace_log` 为空 → executor 未配置关键词无法命中，
  失败任务误归 `command_failed`。可接受：A1.4 主路径（task/search 正常）带
  traceLog，且误归仅影响引导文案粒度。
- **边界 R-b（P3，记录）**：`command_failed` 前端分支当前环境无法 E2E——
  executor 未配置时命令根本不执行（无真实命令失败样本）；待 M3 本地 driver
  落地后补该路径 E2E。当前以单测（分类函数）+ 类型检查覆盖。
- **边界 R-c（P3，记录）**：分类依赖 traceLog 文本——若命令输出/上游日志
  被注入关键词可致误分类；仅影响引导文案渲染，无安全/状态影响（失败判定
  仍以节点状态聚合为准，分类是纯展示增强）。

### 8.10 M0.1 依赖可达性探测结果（2026-09-20 实测）

**结论先行：spg-registry（阿里云镜像仓）可达、构建期基础镜像已就绪；
ant/aliyun 内网依赖（maven/pip/git/miniconda 下载源）全部不可达——确认
走"公共源 + 本地源码"改造路径（与 §8.5 决策一致），M0.2/M0.3 范围不变。**

| 目标 | 探测结果 | 判定 |
| --- | --- | --- |
| `code.alipay.com`（git clone 源） | DNS/TCP 通（110.76.17.67），TLS 握手失败（SSL_ERROR_SYSCALL） | ❌ 不可达（M0.2 clone→COPY 改造确认必要） |
| `artifacts.alipay.com`（maven 私服） | TCP 通，HTTP 无响应 | ❌ 不可达（换公共 mirror） |
| `artifacts.antgroup-inc.cn`（pip 源） | TCP 通，TLS 握手失败 | ❌ 不可达（pip 改本地 KAG 源码，§8.3 决策） |
| `hyperloop.cn-hangzhou...`（miniconda 下载） | HTTP 403（服务在，路径级拒绝） | ❌ 不可用（python 基础镜像已就绪则无需重建） |
| `spg-registry.cn-hangzhou.cr.aliyuncs.com` | `/v2/` 401（正常认证）；`openspg-base:maven-3.8.5-openjdk-8`、`openspg-python:kag-base-0.5.1b3` 均 pull 成功 | ✅ 可达，**基础镜像本地就绪** |
| base 镜像工具链 | openjdk 1.8.0_422 + Maven 3.8.5 + Python 3.8.10 | ✅ 可作构建环境 |
| 公共 maven 源（aliyun public）解析 openspg 主链 | 容器内 `mvn -pl common/util -am install -DskipTests`（公共 mirror，m2 命名卷缓存）→ **BUILD SUCCESS 52s**；`dependency:resolve` 报错仅因 SNAPSHOT 兄弟模块未 install（maven 行为，非依赖缺失） | ✅ **公共源可完整编译 openspg 主链** |

**M0.1 结论（gate 0 通过）**：内网依赖不可达已确认且不构成阻塞——基础镜像
本地就绪、公共 maven 源可编译主链、KAG 源码本地（§8.3）、miniconda 依赖
随 python 基础镜像就绪而消除（D4）。**可进入 M0.2（Dockerfile fork 化改造）**；
构建命令基线：`-f openspg/pom.xml` + 公共 mirror settings + 跳过 fork 私有
模块（D2）。

### 8.11 M0 实施记录（2026-09-20 完成）

**状态：M0 全里程碑通过**——本地可构建 executable jar、重建 local 镜像、
容器替换后回归全绿（4 项目 / 29 spgType / 图 labels / 构建受理 / openkg-webui
概念规则经新 server 查询正常）。

**构建链实际修正（对 §8.5/8.6 的补充与修正）**：
1. **thinker system 依赖修正（关键）**：`reasoner-local-runner` 与
   `core-schema-service` 的 `com.antgroup.kg.reasoner:thinker:0.0.1` 为
   `<scope>system><systemPath>${project.basedir}/../../lib/thinker-0.0.1.jar`
   ——**跨 reactor 依赖时 POM invalid（systemPath 相对路径失效），传递依赖
   （lube 等）全部丢失**，导致 server 编译 "package ...lube.parser does not
   exist"。修正：把 thinker jar `install-file` 进 m2（
   `com.antgroup.kg.reasoner:thinker:0.0.1`）+ 两处 pom 改常规依赖（去
   scope/systemPath）→ 构建通过。
2. **构建顺序**：reasoner reactor（含 lube-api/logical/physical/kgdsl-parser）
   → thinker install → server reactor（含 arks-sofaboot）；scalastyle skip
   （子目录 reactor 找不到根配置）。
3. **`release/python/lib/` jar 缺失（§8.5 D1）判断修正**：executable jar 非
   "缺失待手工补"——`arks/sofaboot` 的 spring-boot repackage
   `<outputDirectory>../../../dev/release/server/target` 自动生成到
   `openspg/dev/release/server/target/arks-...-executable.jar`（314MB）。
   sofaboot 在 **`openspg/server/pom.xml`** 的 modules（不在 openspg/pom.xml）。
4. **镜像构建简化**：因 BUILDER 会全量重编（无 m2 缓存挂载），改为宿主机
   docker run 预构建 jar → runtime 镜像 `FROM openspg-python:kag-base-0.5.1b3`
   + COPY jar（`openspg-server:local`，5.5GB）。避免 docker Hub frontend
   拉取（去掉 `# syntax=` 行）。
5. **KAG worker 装配延后 M2（新增发现）**：本地 KAG 0.8.0 `install_requires`
   含 `mcp==1.6.0` → **requires_python>=3.10**；而 `kag-base` runtime 镜像仅
   `py3.8`（python3.10 无）→ py3.8 上 `pip install openspg_kag` 失败。决策：
   M2 worker 需**更高版本 python 基础镜像**（升 openspg-python tag，或本地
   装 py3.10）——M0 回归不依赖 KAG，不阻塞；§8.3「kag 0.8.0 本地装」保持但
   依赖 py3.10 环境。

**M0 产物**：`openspg-server:local` 镜像；`openspg/dev/release/server/target/
arks-...-executable.jar`；mysql 备份 `/tmp/m0_mysql_dump.sql`；openspgapp
改造点（thinker pom ×2 / 公共 settings `docker/mvn-settings-public.xml`）。

### 8.12 M1 前置结论与 M0 衔接

M0 已为 M1（本地 computing-engine driver）铺好全部接入点：
- **driver 模块位置**：`openspg/cloudext/impl/computing-engine/local/`（上游
  命名空间；M0 已验证 reasoner/server 双 reactor 可公共源构建，M1 新增模块
  进 server reactor 依赖即可）。
- **thinker system 依赖教训**：M1 模块**勿用 system scope 本地 jar**（跨
  reactor POM invalid 坑）；所需依赖一律 `install-file` 进 m2 后常规声明。
- **构建命令基线**（M1 复用）：公共 settings + `-Dscalastyle.skip` +
  thinker 已装 m2；顺序 reasoner → thinker → server（sofaboot）。
- **KAG worker py3.10 决策**（M2 前置）：M1 的 driver 仅做进程管理（不依赖
  python 版本），worker 环境（py3.10 + kag 0.8.0）在 M2 另行装配。

### 8.4 排期结论

P0b 为**独立跨仓库工程**（openspgapp Java + 镜像 + worker 环境），不并入
openkg-webui 阶段交付；M0/M1 需用户确认环境投入（openspgapp 构建链）后启动。
openkg-webui 侧 P0a 可观测 + 本排期即当前边界最大化。

### 8.13 M1 实施记录（2026-09-20 完成）

**状态：M1 全部通过**——本地 computing-engine driver 实现、单测、接入 server
并完成运行时验证（driver 成功注册、子进程执行、构建节点 FINISH）。

M1.1 实现（openspgapp 工作副本 `openspg/cloudext/impl/computing-engine/local/`）：
- `LocalComputingEngineClientDriver.java`：`scheme=local`，继承
  `CachedCloudExtClientDriver`，static 自注册到 `ComputingEngineClientDriverManager`。
- `LocalComputingEngineClient.java`：核心——`submitBuilderJob` 用
  `/bin/sh -c <extension.command>` 起子进程，`ConcurrentHashMap` 进程注册表，
  `queryStatus` 状态机（RUNNING→SUCCESS/FAILED/NOTFOUND，作业级幂等复用 taskId，
  守护线程 drain），`stop` 终止；仅支持 KAG_COMMAND，其他类型登记即时失败。
- `LocalComputingEngineClientTest.java`：6 个状态机/进程测试（成功/失败/未知/
  不支持类型/幂等重提/工作目录日志位置）。
- pom：`cloudext-impl-computing-engine-local` artifact。

M1.2 单测（容器化 maven，openspg-m2 卷，公共 settings）：
- **卡点 root cause**：父 POM 同时引入 junit 4 与 junit-jupiter，surefire 自动
  选 JUnitPlatformProvider，缺 vintage 引擎时 JUnit4 测试不执行（`Tests run: 0`）。
  local 模块 pom 显式加 `junit-vintage-engine:5.7.1`（test scope）后
  **`Tests run: 6, Failures: 0`**。

M1.3 接入 server + 运行时验证：
1. `core-scheduler-service` pom 增 `cloudext-impl-computing-engine-local` 依赖
   （与 interface/objectstorage impl 并列模式）。
2. **依赖链 invalid 缺陷**：因多模块 duplicate/systemPath 缺陷，`core-scheduler-service`
   的传递依赖被判 invalid 而丢失（连 computing-engine 接口 jar 都从未进 fat jar，
   解释了历史 "cannot find driver"）。`-pl` 单模块打包即复现；**必须以
   `-am` 同 reactor 打包**才保留传递依赖。另在 sofaboot 顶层直接声明
   `cloudext-impl-computing-engine-local` 兜底。
3. **ServiceLoader 注册 root cause**：`ComputingEngineClientDriverManager` static
   块经 `DriverManagerUtils.loadDrivers` 用 `ServiceLoader.load` 加载
   `META-INF/services/...ComputingEngineClientDriver`（接口模块 resources 内）。
   源码只列了**不存在的** `aistudio.AiStudioClientDriver` → ServiceLoader 对首个
   提供者抛 `ServiceConfigurationError`，被 Manager 的 `catch(Throwable){}` 吞掉
   导致迭代中止 → **任何 driver 都注册不上**。修复：声明文件改为仅
   `com.antgroup.openspg.cloudext.impl.computingengine.local.LocalComputingEngineClientDriver`。
4. **运行时验证**（临时容器 `m1-validate-server`，同 `openspg-server:local`
   镜像 + 挂载新 fat jar + `--cloudext.computingengine.url=local://exec`）：
   - 日志 `registerDriver: LocalComputingEngineClientDriver@...` ✓
   - `local builder task ... finished with exitCode 0`（`echo M1-VALIDATION-CLOVER`）✓
   - 失败命令 `exit 7` → `exitCode 7` 被正确捕获 ✓
   - 调度器节点 `Builder computingEngineAsyncTask` 与 `PostProcessor
     kagCommandPostSyncTask` 均 **FINISH**（实例级 status 仍 WAITING，为 P0a
     已记录的调度器 quirk，成败按节点级判定）✓ —— **P0b 构建闭环打通**。

遗留（M2 前置）：
- **openspgapp 为工作副本（非 git 仓库）**，M1 改动无法在 openspgapp 侧
  branch/commit/PR；需用户确认其版本管理方式（git init/关联）后再走跨仓库提交。
- 运行中的 `release-openspg-server` 仍跑旧 jar（无 local driver）；本次仅验证于
  临时容器，**尚未重建镜像/推进生产**——是否将新 fat jar 落入正式部署由用户决定。
- M2：重建镜像并入新 driver；装配 worker 环境（py3.10 + `kag` CLI + `kag_config.yaml`），
  `--cloudext.computingengine.url=local://exec` 后验证真实 `kag builder`。

### 8.14 M2 实施记录（2026-09-20 完成，worker 环境 + 集成验证）

**状态：M2 达成（worker 环境 + driver 接入真实 kag 运行时）**；真实 KG 构建的
项目/schema 装配归入 M3 E2E。

M2.0 门控探测：
- 镜像 `openspg-server:local` 仅系统 py3.8、无 conda、无 kag CLI（`/openspg_venv`
  为 kag **0.5.1-beta3**/py3.8）；需按 §8.3 决策装配本地 KAG 0.8.0（py3.10）。
- 网络全通：pypi.org / 阿里云 pip / Ubuntu ports apt / maven central 均 200。
- aarch64 miniconda（公共源）不可达 → 改引 `python:3.10-slim-bullseye`
  （glibc 2.31 与 focal 匹配；`python:3.10-slim` bookworm 的 glibc 2.36 跑不起来）。
- 关键 de-risk：openkg-webui 演示 vectorizer（`ai.wust.edu.cn/gpustack`）**容器内可达**；
  gpustack 同时托管 vectorizer（`Qwen3-Embedding-8B`，dims4096）与 chat LLM
  （`DeepSeek-V4-Flash-*`/`qwen2.5-72b-instruct`），OpenAI 兼容 → 可作真实 build 的
  LLM+vectorizer。

M2.1 worker 环境（worker 容器内装配，非内网源）：
- 拷入 py3.10（bullseye `/usr/local`）→ `/opt/py310` → venv `/opt/kag-venv`（py3.10.18）。
- 本地 KAG 0.8.0 源码拷入 `/opt/KAG`；`pip install -e /opt/KAG --no-deps`（setup.py
  develop）+ `pip install -r /opt/KAG/requirements.txt`（pypi.org、--no-build-isolation，
  mcp==1.6.0/grpcio 等以 aarch64 wheel 安装，EXIT=0）。
- `kag` console script 就绪（`kag builder` 子命令可用，`kag --version` 正常）。
- **固化**：`docker commit m2-worker-server → openspg-server:m2`，重入验证 kag 0.8.0 ✓。

M2.2 集成验证（local driver 真实跑 kag 运行时）：
- 经 openkg-webui 提交 KAG_COMMAND `/opt/kag-venv/bin/kag --version` → 调度器 →
  local driver `/bin/sh -c` 子进程 → **exitCode 0** + `registerDriver:
  LocalComputingEngineClientDriver@...` 日志 + 调度节点 FINISH。证明
  **openkg-webui → scheduler → local driver → py3.10+kag 全链路闭环**。

M2.3 真实 `kag builder` 边界确认：
- `kag builder`（0.8.0）为**分布式提交器**（git clone+checkout+pip install+entry_script
  打包后 POST 回 server）；真正本地执行走项目内 entry_script（如 baike `builder/indexer.py`
  的 `BuilderChainRunner`）。
- baike 示例端点为 gpustack 改写后，`indexer.py` 在容器内**正确初始化**（连上
  host_addr、解析 KAG project）——仅失败于 `cannot find project id=7`（该 project 不存在）。
  即 kag 运行时在容器内完全可用；唯一缺口是**匹配的 SPG 项目 + schema**（m0probelive
  图库仅有 Person/Organization，无 KAG 的 Chunk/KnowledgeUnit 类型）。

M2 结论与 M3 边界：
- M2 交付：worker 环境（`openspg-server:m2`）+ driver 接入真实 kag 运行时的集成闭环。
- M3 E2E（待做）：新建带 KAG schema 的项目（注册 Chunk/KnowledgeUnit 等）+ 用独占
  图库跑真实 `kag builder` → 验证图写入 Neo4j + openkg-webui 成功 badge + 失败路径 traceLog。
  外部依赖（gpustack LLM/vectorizer 凭证可达）已 de-risk，schema 装配是 M3 主体工作量。
- 部署（2026-09-20 已执行）：`release-openspg-server` 换为自包含镜像
  `openspg-server:prod`（= m2 worker 环境 + 新 fat jar 烤入 entrypoint 路径），启动加
  `--cloudext.computingengine.url=local://exec`；同网络/端口/参数无损重建，项目列表 200。
  生产链路验证：driver `registerDriver` + 子进程跑 `kag --version` **exitCode 0**。
  即 **P0b 构建闭环在执行层已在生产启用**；剩余为 M3 的项目/schema 装配。

### 8.15 computing-engine 必要性复核 + 决策（2026-09-20）

**复核结论：自包含本地构建必须提供 computing-engine 实现。** 证据：
1. 调度器 `ComputingEngineAsyncTask` 的 `submit`/`getStatus`/`stop` **全部**走
   `ComputingEngineClientDriverManager.getClient(cloudext.computingengine.url)`——
   **无任何进程内/同步/本地回退路径**；不开 computing engine，构建即无法真正执行
   （本地演示长期 "cannot find driver" 的根因）。
2. 权威 executor 为 **aistudio**（阿里云 AIStudio 云集群）；fork 的 `AiStudioClient`
   依赖**私有构件**（`AIStudioWorkflowClient`/`kubemaker-client-java`）→ 无法进公共
   仓库。上游 `~/project/openspg` `cloudext/impl/` **无** computing-engine 实现，仅
   接口 + 一条指向不存在类的 ServiceLoader 残留（其 ClassNotFound 使 ServiceLoader
   迭代中止，任何 driver 都注册不上）。
3. fork 的 `LocalComputingClient` stub **未实现**（submit/query 返回 null）且属另一套
   接口（`com.antgroup.openspgapp...ComputingClient`），与调度器所用的 computing-engine
   接口不同、未接线，满足不了构建执行。

**决策：保留本地子进程 driver（暂不 PR）**——自包含本地闭环的唯一无云路径；
M1 已实现并运行时验证（registerDriver + `kag --version` exit 0 + 节点 FINISH）。
- 上游 PR 准备已完成（`~/project/openspg` 分支 `feat/computing-engine-local-driver`，
  commit `6305661e`，`-am` 构建 + 6 单测绿，注释英文、spotless 通过），已推送至
  simongu fork；**PR 暂缓创建**，待后续决定。
- 若后续转方向，二选一：① 对上游 openspg 提交该 PR；② 接 aistudio 云（需阿里云
  AIStudio 凭证，非本地自包含）。

### 8.16 M3 E2E 实施记录（2026-09-20 完成——真实 kag builder 构建闭环全通）

**状态：M3 全里程碑达成**——新建 KAG 项目+schema → 真实 `kag builder` 经 local
driver 执行（LLM 抽取+向量化）→ 图写入 Neo4j → 调度节点 FINISH。

M3.1 项目与 schema：
- 生产容器已具备 `knext` CLI；用示例 `example_config.yaml` 改造为 gpustack 端点 +
  新项目 `M3BuildDemo`（namespace 同名，id 5，zh），`knext project create` 成功
  （在 `/opt/M3BuildDemo` 生成 KAG 框架）。
- `knext schema commit` 成功：注册 M3BuildDemo 全部实体类型（Person/Organization/
  Works/Event/…）+ 内置类型（Chunk/KnowledgeUnit/Summary/Outline/AtomicQuery/Table）。

M3.2 关键坑位（pemja 桥接）：
- server 的 `PemjaUtils` 用 `python.exec` 启动内嵌 python 做项目/schema 的 python
  侧桥（bridge）。三连环修复：
  1. **pemja 版本对齐**：fat jar 内置 Java pemja **0.3.0** 与可装 wheel 的 python
     pemja **0.6.2** 不匹配（`Failed to find PemJa Library`）→ 把
     `openspg/pom.xml` 的 pemja 升到 **0.6.2** 重编 sofaboot（Java/python 对齐）。
  2. **PYTHONHOME + base 解释器**：pemja 内嵌 python 启动时 prefix 解析错
     （venv 软链 + 挪位导致 `sys._base_executable=/usr/bin/python3` 找不到
     `encodings`）→ 把 venv 包铺到 base site-packages，容器 `-e PYTHONHOME=/opt/py310/usr/local`，
     `--python.exec=/opt/py310/usr/local/bin/python3.10`。
  3. **bridge 模块 + mcp 遮蔽**：`bridge` 需顶层 import（在 KAG 的 `kag/bridge/`），
     但把 `/opt/KAG/kag` 加进 python.paths 会遮蔽 `mcp` SDK（`kag/mcp/` 同名）→
     将 `bridge/` 单独拷入 site-packages，python.paths 仅 `/opt/KAG`。
- 内存：server `-Xmx8192m` 在 16G 主机 OOM 崩溃 → 降到 **-Xmx4096m**。

M3.3 真实构建（经 local driver 提交）：
- `POST /public/v1/builder/kag/submit`（projectId 5，command=`cd /opt/M3BuildDemo &&
  /opt/py310/usr/local/bin/python3.10 builder/indexer.py`），driver 起子进程执行
  kag builder：读 2 个 txt → gpustack LLM 抽取（chat/completions 200）→ Qwen3 向量化
  （embeddings 200）→ **`buildKB successfully`，2/2 成功，exitCode 0**。
- **Neo4j 图写入验证**（`m3builddemo` 库）：Others 1127 / KnowledgeUnit 520 /
  SemanticConcept 380 / AtomicQuery 204 / Chunk 40，共 2200+ 节点。
- 调度节点：**Builder（computingEngineAsyncTask）FINISH、PostProcessor
  （kagCommandPostSyncTask）FINISH**；实例级 status 仍 WAITING（P0a 已记录 quirk）。
- 生产容器已 `docker commit` 固化（`openspg-server:prod` 最新层含 M3 全部环境）。

M3.4 结论：
- **P0b 构建闭环端到端打通**（openkg-webui 受理层 + 调度器 + local driver + 真实 kag
  builder + 图写 Neo4j + 可观测状态），openkg-webui 侧零代码改动（P0a 已覆盖展示）。
- 后续若做 openkg-webui 受理→该项目构建的展示回归，可直接走既有 `submit_build` 端点
  （任务列表 live 合并 P0a 已交付）。

### 8.17 M4 收尾评审 + 计划归档（2026-09-20）

**代码评审（local computing-engine driver，上游分支 `feat/computing-engine-local-driver`）**：
- 结论：**通过，无阻断问题**。完整审阅 `LocalComputingEngineClient`（英文注释、
  spotless 通过、`-am` 构建 + 6 单测绿、M3 运行时端到端已验证）。
- 关键点复核：幂等复用（同 BuilderJob 未结束复用 taskId，防重试重复起进程，有单测）；
  内存注册表重启丢失 → 调度器按 NOTFOUND 重提交（设计已记录，可接受）；非 KAG_COMMAND
  即时 FAILED（状态机表达）；超时 STOP（124）；`redirectErrorStream` 归并 stdout/stderr
  落日志；子进程环境仅透传非密连 URL（不注入凭据）。
- 备注：driver 注入的 `SPG_*` 连接 URL 环境变量为「透传备用」，真实 kag builder 经
  `openspg_graph_api` 走 host_addr + kag_config 读取图库，两者并存不冲突。

**计划整体状态**：P0a / P0b(M0–M3) / B / C1 / C2 / D1 全部交付并实测；M3 打通真实
`kag builder` 构建闭环（Neo4j 落图 + 节点 FINISH）。openspgapp/上游 PR 按 §8.15 决策
**保留本地 driver、PR 暂缓**（上游分支已备好，随时可推）。
**归档**：本计划目标全部达成；后续可选演进（openkg-webui 受理→新项目构建展示回归、
图浏览 reason DSL 深化等）不在此计划范围。

### 8.18 部署层零 openspgapp 依赖（2026-09-20 完成）

**目标**：生产镜像改为从上游 `~/project/openspg`（分支 `feat/computing-engine-local-driver`）
构建，彻底消除对 openspgapp fork 的部署层依赖。

- openkg-webui 运行时审计（代码层面本就零依赖）：全部 18+ 端点均走标准 `/public/v1/*`
  公开接口（project/schema/graph/reason/builder/scheduler/concept）；前端 `web/` 无
  openspgapp 引用；`spg_server_url` 可指向任意 OpenSPG server。代码库中 openspgapp
  仅出现在 `docs/`（合规红线声明）与 `scripts/kag_m0/`（开发期审计工具），非运行时。
- 上游构建前置补丁（同一 PR 自然组成，commit `64fd9277`）：
  1. `pom.xml` pemja **0.3.0 → 0.6.2**（Java 侧与生产 python pemja 0.6.2 wheel 对齐，
     否则 pemja 桥 `Failed to find PemJa Library`）；
  2. `server/arks/sofaboot/pom.xml` 顶层声明 `cloudext-impl-computing-engine-local`
     （规避中间 POM invalid 导致的传递依赖丢失）。
- 构建：`mvn -pl server/arks/sofaboot -am package` → executable jar（含
  local driver / computing-engine 接口 / core-scheduler-model / **pemja 0.6.2**，
  **无任何 openspgapp 产物**）。固化进 `openspg-server:prod` 新层，镜像内 jar
  sha256 与源构建一致。
- 生产容器重建（参数不变：PYTHONHOME、python.exec=/opt/py310/usr/local/bin/python3.10、
  python.paths=/opt/KAG、computingengine.url=local://exec、-Xmx4096m），回归：
  - server 启动 200、项目列表正常；
  - local driver `registerDriver` + kag 子进程 **exitCode 0**；
  - 真实 `kag builder` **exitCode 0、2/2 成功**（checkpoint 复用 M3 已建 40 records，
    图库/项目数据不受镜像重建影响）。
- **结论**：生产部署 = 上游 OpenSPG 代码 + 自研 local driver（上游分支已备好）+ worker
  环境，对 openspgapp **零依赖**；openspgapp 仅存于开发期审计脚本的历史路径，不进入交付。

### 8.19 未完成任务清单实施（#3–6、#9–10，2026-09-20 逐项评审后实施）

| # | 任务 | 评审结论 | 实施 |
|---|---|---|---|
| #3 | 反馈功能 | 管理面无独立反馈需求（§5 已记录） | 维持不做（本记录固化决策） |
| #4 | 列表页 `failure_reason` | §8.9.2 收敛（列表无 traceLog 避免误导）合理；前端展开态已消费详情 `failureReason`（KagTasksPage），解析与边界测试已有 | 后端列表测试固化「列表不计算」断言（test_list_project_builds_merges_live_status）；前端无需改 |
| #5 | 构建/导入面板文案动态化 | 原文案「remote executor must be configured」在本地 driver 落地后过时 | MemberBuildPanel + KagImportPanel 挂载时拉最近 build live_status：success→「本地 executor 就绪」绿提示；failed→琥珀提示；无/未知→中性。i18n +4 key（zh/en） |
| #6 | `failure_reason` 词表扩展 | driver 落地后真实失败归 command_failed；executor 未配置词表补配置提示形态 | `_EXECUTOR_UNCONFIGURED_HINTS` +3 词（`computingengine.url`/`computing engine url`/`no driver registered`）；测试覆盖新词 + 真实 builder 失败不误伤 |
| #9 | openkg-webui 受理→新项目构建展示回归 | 走既有 `POST /projects/{id}/build`（same-origin+membership） | E2E：经 openkg-webui 提交真实 `kag builder`（project 5）→ 任务列表 live 合并 **success** 展示 ✓ |
| #10 | 图浏览 reason DSL 深化 | C1 已交付一跳；深化做契约安全的 2-hop 模板（节点类型全限定，无新 DSL 语法风险） | model.ts `twoHopDsl` 纯函数 + GraphExplorerSection「2-hop template」按钮 + i18n（zh/en）+ kag-model 测试 |

**质量门**：后端 `test_build_observability.py` **20 passed**；前端 `check:fast`（tsc + eslint + 690 vitest + i18n parity）**exit 0**。
**遗留**：#3 为决策固化（不做）；#4 维持收敛（列表不计算，详情分类已落地）。

**代码评审增补（质量+安全，2026-09-20 实施后）**：
- **P1 发现并修复（reasoner DSL 契约）**：活图库实测 twoHop/Relation 模板生成的 DSL
  报 `SchemaException: Cannot find n`——reasoner **不支持关系 label 约束（-[p:rel]->）与
  目标/中间节点类型约束（(o:Type)）**，仅起始节点可带类型。影响 twoHopDsl（#10 新增）
  及**既有 C1 的 Relation template 按钮**（`-[p:workFor]->(o:B)` 自始不可执行）。
  修复：twoHopDsl 改为 `MATCH (n:A)-[p1]->(x)-[p2]->(o)`（仅起始类型）；Relation template
  改为 `MATCH (n:A)-[p]->(o)`；契约注记同步到 model.ts / openspg_client.py / 图浏览说明文案
  （i18n zh/en）。修复后活图库实测：Relation 503 行、2-hop 32 行 ✓。
- **安全核查**：twoHopDsl 类型来自 server schema（可信），无用户输入注入；图浏览 DSL 输入框
  为既有只读查询面（风险不变）；#5 提示文案走 `t()` 渲染（无 HTML 注入）；新增请求仅走既有
  `GET /projects/{id}/builds`（read + membership）无新权限面。
- **质量核查**：`list_tasks` 文档明确「新在前」→ `rows[0]` 即最近构建 ✓；面板 useEffect 有
  `alive` 卸载防护 ✓；词表子串匹配误伤概率低（配置提示词）✓。

### 8.20 openkg-webui ↔ openspgapp 功能差异细化评审（2026-09-20）

> 依据：openkg-webui `web/features/kag/` + `kag.py` 代码盘点 ↔ openspgapp 控制台（后端
> Controller + 前端 umi/antv-g6 编译产物）盘点。完整度分三档：**✅细节完整 /
> 🟡表面或子集（列缺失）/ ○未做**。本清单区分"功能有无"与"细节深度"。

| 域 | openkg-webui 现状 | 完整度 | 关键缺失（openspgapp 有） |
|---|---|---|---|
| 项目/知识库 | 列表/创建/详情、owner+成员、admin 编辑成员、membership 过滤 | 🟡 | 删除/改名、成员角色细分、关键字/分页 |
| **Schema/知识模型** | 只读类型树（含继承/ownProperties）、**仅关系增删**（`alterSchema` 仅 `add/delete_relations`） | 🟡（缺口最大之一） | 属性/类型/Concept 编辑、schema 导出、中英映射 |
| 概念建模/规则 | belongTo/leadTo 浏览/增删、`belong_to_ready` 门禁（400 拒提+UI 禁用）、双路径、上游失败降级 | ✅ | 完整概念树（getConceptTree/detail/expand）浏览 |
| 图浏览 | DSL 输入+Node/Relation/2-hop 模板、表格、cytoscape 文本画布、节点点击详情、1/2 跳、200 截断、30 节点封顶 | 🟡 | 搜索/抽样、属性/边详情深度、antv-g6 级图形；受 reason DSL 契约限制（§8.19 实测） |
| 数据接入/构建 | 引导式 KAG_COMMAND 模板+占位拦截、真实构建经 local driver、任务可观测、executor 就绪提示 | 🟡 | **文件上传数据源**（无公开端点）、任务删除/重试/日志下载/进度 |
| 推理/对话 | 推理经聊天 agent + KAG 求解（M5-B mcp 冻结契约）；无独立管理 UI | 🟡 | 独立对话任务管理面（dialog submit/检索）、reason/thinker 任务 UI |
| **App 编排** | 无 | ○ | 应用列表/创建/编排/部署/接入/apiKey |
| 权限/多租户 | 项目成员/owner、membership ACL | 🟡 | 完整授权（角色/资源/类型筛选）、账号生命周期 |
| 设置/模型/运维 | OpenSPG 地址/Bridge 连接设置 | 🟡 | 数据源/图存储/监控面板、完整模型服务管理面 |
| 统计/反馈/教程 | D1 构建统计 chip | 🟡 | 完整统计面板、反馈（决策不做）、教程 |

**结论**：openkg-webui 为"管理薄层"，多数域是可用的**子集**而非完整控制台。差距最大的三块：
① **Schema**（仅关系编辑）；② **图浏览**（受 DSL 契约 + 精简渲染限制）；③ **App 编排 /
文件上传 / 完整权限**（完全未做）。**质量最高（细节完整）的一项为概念规则**（门禁 +
降级 + 测试）。
