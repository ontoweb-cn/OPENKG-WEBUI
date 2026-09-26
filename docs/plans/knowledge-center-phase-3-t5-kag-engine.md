# 知识中心 Phase 3 T5 细化方案：KAG 作为第二知识引擎

- 日期：2026-09-23
- 状态：**细化待评审**（评审通过后实施）
- 依据：[knowledge-center-port-design.md](../knowledge-center-port-design.md) §十二 TODO（KAG 纳入统一管理）+
  [Phase 3 引擎抽象](knowledge-center-phase-3-engine-abstraction.md) §六评审 D3（KAG 走 openkg-webui 侧 provider）+
  T3 域模型收口后的现状
- 前置：T1/T2（引擎抽象）与 T3（域模型/错误映射）已交付

## 一、现状核对（实施前实测，决定方案边界）

| 维度 | intellect-rag（现有） | KAG | 差异性质 |
| --- | --- | --- | --- |
| "知识库"概念 | dataset（文档集合） | **OpenSPG project**（图谱项目） | 资源模型不同 |
| 文档 | 上传/解析/分块/预览 | **无文档概念**（有 build 图构建任务） | KAG 缺 4 项能力 |
| 检索 | 向量+关键词 hybrid（`/search`） | **图查询**（`graph/query`、`concept/rules`） | 语义不同 |
| 创建必需参数 | name/description/permission | **namespace**（Neo4j 约束，3-64 字母数字）+ **embedding_model_id** | 创建表单不可复用 |
| 响应形状 | RagFlow 信封（T3 已归一） | **已是域形状**（`{"projects": [...]}`） | KAG 无需归一 |
| 鉴权 | agent-loop identity（P1-1） | **openkg-webui 原生 multi_user**（`access.py`/`member_store.py`） | **身份平面不同** |
| 聊天绑定 | `rag_binding`（runs rag 块） | **`kag_grounding_block`**（已实现） | KAG 已有 |
| MCP 绑定 | `mcp_binding`（.mcp.json 注入） | **`ensure_session_mcp_config`**（已实现，kag-bridge） | KAG 已有 |
| 管理 UI | KC 页面（本方案产出） | **已有 9 个组件的完整页面组**（schema 编辑/图浏览/成员/builds） | KAG 更丰富 |
| 端点 | 25（`/api/knowledge-center/*`） | 15（`/api/kag/*`） | 并存 |

**结论**：KAG 的"引擎接入"里，**聊天与 MCP 两个绑定早已存在且工作**（§十二 要求"不动"），
真正缺的是**管理面的目录统一**。同时 KAG 的资源模型（图谱项目）与 KC 的文档模型
不可通约——**强行统一详情页会降级 KAG 现有 UX**（丢掉 schema/图浏览/成员管理）。

## 二、方案范围（决策 D1：三选一）

| 选项 | 内容 | 成本 | 评价 |
| --- | --- | --- | --- |
| **A. 仅注册不合并**（最小） | engine registry 加 KAG 描述符；`/api/knowledge-center/engines` 暴露目录；UI 不加任何东西 | S | 无用户可见价值——目录没人看等于没做 |
| **B. 统一目录 + 分派详情**（推荐） | KC 列表页**合并展示**两个引擎的资源（KAG 标 engine 徽标）；点击按引擎**分派**到各自详情页（intellect-rag → KC 详情；KAG → 既有 `/kag/projects/{id}`）；创建流程按引擎分派（KAG 创建跳既有页面） | M | 达成 §十二 的"统一管理/统一入口"，且**不牺牲** KAG 现有能力 |
| C. 全量统一 | KAG 也走 KC 的详情页与创建表单 | L | **不建议**：需为 KAG 重建 schema/图浏览/成员 UI，且 KC 的文档模型无法表达图谱项目 |

**本方案取 B**。验收对齐 §十二："KC 引擎目录加第二个条目不需要改动知识中心核心"
——核心（协议/registry/列表聚合/分派规则）写一次，第三个引擎只需 provider + 描述符。

## 三、设计

### 3.1 引擎描述符（后端，新增只读端点）

```python
# engines/base.py 扩展
@dataclass(frozen=True)
class EngineDescriptor:
    engine_id: str
    display_name: str
    capabilities: frozenset[str]
    configured: bool  # 该引擎是否已配置可用（KAG 看 kag_enabled()）
    detail_path_template: str  # UI 分派用："/knowledge-center/{id}" | "/kag/projects/{id}"
    create_in_knowledge_center: bool  # KAG=False（创建需 namespace/embedding，跳既有页）
```

`GET /api/knowledge-center/engines` → `[EngineDescriptor]`（仅返回 `configured` 的引擎）。

**`detail_path_template` 的取舍**：把路由模板放后端描述符，前端做模板替换（`{id}` → 资源 id）
——避免在 KC 列表组件里硬编码 `if engine === "kag"`（评审时的核心争议点，建议采纳；
若评审认为后端不该知道前端路由，备选是前端维护 `ENGINE_DETAIL_ROUTES` 映射表，二选一）。

### 3.2 列表聚合（后端合并，非前端多请求）

`GET /api/knowledge-center/datasets` 语义扩展：

- 无 `engine` 参数 → **合并所有 configured 引擎的资源**（各 provider `list_datasets()` 并在
  应用层合并；单引擎失败不阻塞另一引擎——逐引擎 try/except，失败的引擎以 `engine_errors` 字段回报）
- `?engine=<id>` → 仅该引擎（现有行为不变）

`KnowledgeDataset` 增加 `engine_id: str = ""` 字段（T3 域模型的向后兼容扩展）。

**N+1 与分页**：合并列表的分页语义按"每引擎各自取前 N 条后合并"实现（避免跨引擎全局分页的
复杂度）；`total` 为各引擎之和。**已知取舍**：跨引擎排序不保证（KAG 项目与 dataset 无共同排序键），
文档中注明。

### 3.3 KAG provider（`engines/kag.py`）

实现协议的能力子集，**不含** upload/structured_upload/logs/preview/sources：

| 方法 | 实现方式 |
| --- | --- |
| `list_datasets()` | 复用 `services/kag/`：`list_projects()` + `filter_projects_by_access()`（**沿用既有 ACL**，不新写权限） |
| `get_dataset(id)` | `get_project(id)` |
| `create_dataset()` | **抛 `EngineError(INVALID, "KAG projects must be created via the KAG console")`**——创建跳既有页（见 3.4） |
| `delete_dataset(id)` | 复用既有删除（如存在）；否则声明该能力缺失 |
| `search(id, question)` | 复用 `graph/query`；**语义提示**：结果为图查询行而非相似度分块 → 映射为 `SearchResult`（`chunks` 承载行的 JSON 文本，`similarity=0`），并在 `SearchResult` 加 `kind: str = "chunks" \| "graph_rows"` 让 UI 区分呈现 |
| `chat_binding()` | **委托既有 `kag_grounding_block()`**（§十二：不动执行面） |
| `mcp_binding(workdir)` | **委托既有 `ensure_session_mcp_config()`** |

**capabilities 声明**：`{CAP_SEARCH, CAP_CHAT_BINDING, CAP_MCP_BINDING}`（+ `CAP_DELETE` 视delete 实现）。

### 3.4 KC 前端的引擎感知（最小改动）

1. `features/knowledge/api.ts` 增 `fetchEngines()`；列表页在挂载时取一次
2. **列表条目**按 `engine_id` 显示徽标（复用现有 UI 词汇，无新组件）
3. **点击分派**：按该引擎的 `detail_path_template` 生成 href（`router.push`）
4. **"新建知识库"按钮**：若已配置引擎中只有 intellect-rag 支持 KC 内创建 → 保持现表单；
   若 KAG 也在列表中 → 按钮改为下拉（"新建文档知识库" / "前往 KAG 控制台"），
   后者 `router.push("/kag")`
5. **详情页不动**：KAG 资源不进 KC 详情页（保住 schema/图浏览/成员）

### 3.5 设置面（§十二"统一设置面"）

`/settings/knowledge` 页顶部加"知识引擎"区块：列出 configured 引擎 + 各自配置入口链接
（intellect-rag → 本页既有的连接设置；KAG → `/settings/kag`）。
**不合并**两个 settings 块（`knowledge` 与 `kag` 的配置项语义正交，合并会造成耦合）。

### 3.6 身份平面的不对称（必须文档化）

KAG 走 openkg-webui 原生 multi_user（membership），intellect-rag 走 agent-loop identity
（P1-1）。**两者不是同一身份源**——这是既有事实，本方案不强行统一（统一需要重写 KAG 的
ACL 层，代价远超收益）。在方案与部署文档中显式记录，避免后续误判为"漏了身份对齐"。

## 四、任务拆分

| # | 任务 | 落点 | 估力 |
| --- | --- | --- | --- |
| T5.1 | `EngineDescriptor` + `list_engines()` + `/engines` 端点 + 描述符测试 | engines/base, registry, routers/knowledge | S-M |
| T5.2 | `KnowledgeDataset.engine_id` + 列表聚合（逐引擎容错 + `engine_errors`） | engines/models, routers/knowledge | M |
| T5.3 | `KagEngine` provider（list/get/create-拒绝/delete/search/两绑定委托） | engines/kag.py | M-L |
| T5.4 | 前端：`fetchEngines` + 徽标 + 分派路由 + 新建下拉 | features/knowledge | M |
| T5.5 | 设置页"知识引擎"区块（链接到 `/settings/kag`） | features/settings | S |
| T5.6 | 契约再生成 + 测试（provider 单测 + 列表聚合 + 分派）+ live 验收 | 全链 | M |

提交切分：T5.1+T5.2（后端聚合，UI 未动）→ T5.3（KAG provider）→ T5.4+T5.5（前端）
→ T5.6（契约与验收）。

## 五、验收标准

1. KC 列表页同时显示 intellect-rag datasets 与 KAG projects（各带 engine 徽标）；
2. 点 intellect-rag 条目 → KC 详情页（既有行为不变）；点 KAG 条目 → `/kag/projects/{id}`；
3. 未配置 KAG 时，列表只显示 intellect-rag，且"新建"按钮行为与现在一致（**零回归**）；
4. 单引擎故障不阻塞另一引擎的列表（`engine_errors` 可见）；
5. KAG 的 schema/图浏览/成员/构建/聊天 grounding/MCP 注入**全部不受影响**；
6. 门禁：后端全量 + 前端 typecheck/lint/contracts/i18n/vitest 全绿；live 五链路复验。

## 六、风险

| 风险 | 缓解 |
| --- | --- |
| **资源模型不可通约导致 UI 混乱**（图谱项目混在文档 KB 列表里） | engine 徽标 + 分组可选（评审定：混排 vs 分组） |
| KAG provider 复用既有 ACL 时误改权限语义 | 只调用 `filter_projects_by_access`，**不新增**任何权限判断 |
| 列表聚合引入跨引擎分页/排序的隐含语义 | 明确"各取前 N 合并、不保证全局排序"，文档化 |
| `detail_path_template` 放后端被质疑分层 | 备选：前端映射表（评审二选一，见 D2） |
| KAG 创建不可在 KC 内完成，用户预期落空 | 新建下拉显式给"前往 KAG 控制台"入口 + 说明文案 |

## 八、技术评审记录（2026-09-23，方案自评审）

**总评：范围选择（方案 B）与"不动执行面"的判断正确；但实测推翻了三处设计细节，须修订。**

### P1（必须修订）KAG **没有项目删除能力**——`CAP_DELETE` 不可声明

方案 §3.3 写"`delete_dataset(id)`：复用既有删除（如存在）；否则声明该能力缺失"。
实测：`api/routers/kag.py` 的 4 处 "delete" **全部是 schema 关系删除**
（`delete_relations`），**没有任何 project 删除端点**。

**修订**：KAG provider **不实现** `delete_dataset`、`capabilities` **不含** `CAP_DELETE`；
`delete_dataset()` 若被调用应抛 `EngineError(INVALID, "KAG projects cannot be deleted via the knowledge center")`（显式拒绝优于静默）。

### P2（必须修订）身份需要 **user 对象**而非 user_id——`EngineContext` 不够

方案 §3.3 复用 `filter_projects_by_access()`，其签名实测为：

```python
def filter_projects_by_access(user: Any, projects: list[dict], *, kag_configured: bool)
```

它需要的是 **user 对象**（内部做 `_is_admin(user)` 角色判断），而 `EngineContext`
当前只有 `user_id: str`。这暴露了两引擎的**身份形态差异**：intellect-rag 需要
字符串 id（送 agent-loop identity 解析），KAG 需要对象（本地 ACL 判定）。

**修订**（建议）：`EngineContext` 增加 `user: Any = None` 字段——provider 优先用
`ctx.user`，缺省回落到 `get_current_user()`（请求作用域安全）。这比"provider 自己
拿请求上下文"更符合 P2-3 的上下文绑定设计，且把差异显式化而非隐藏。

### P3（设计修订，推翻原方案）KAG 的 `search` 不应在 T5 实现

方案 §3.3 计划把 `graph/query` 映射为 `SearchResult`（行的 JSON 文本作 chunk、
`similarity=0`，并加 `kind` 字段）。实测 `graph/query` 返回
`{rows, row_count, truncated}`——**行 ≠ 分块**，该映射是"强凑"：
- 语义上误导（图查询行被呈现为"相似度分块"）；
- D4 已决定**不暴露**给任何 UI，映射无人消费；
- KAG 真正的检索路径是既有 `kag_solve`/`kag_reason` MCP 工具（已工作）。

**修订**：T5 的 KAG provider **不实现 `search`**（`capabilities` 不含 `CAP_SEARCH`）；
`SearchResult.kind` 字段的扩展**一并撤销**（不引入无人消费的契约字段）。
未来若确有统一检索 UI 需求，再以独立设计引入（届时优先考虑 `rows` 结构化字段而非
伪装成 chunks）。

### 复核确认（成立的部分）

| 方案论断 | 核实 |
| --- | --- |
| KAG 响应已是域形状（`{"projects": [...]}`） | ✓ 无需信封归一 |
| KAG 聊天/MCP 绑定已存在且完整 | ✓ `kag_grounding_block` + `ensure_session_mcp_config`（§十二要求不动） |
| KAG 创建需 namespace + embedding（表单不可复用） | ✓ `KagProjectCreateRequest` 实测含 `namespace`（Neo4j 约束）+ `embedding_model_id` |
| `kag_enabled()` 可作 `configured` 判据 | ✓ 支持 http/stdio 双形态 |
| KAG settings 页存在（`/settings/kag`） | ✓ 可用于 §3.5 的链接 |
| KC 列表徽标插入点存在 | ✓ `KnowledgeHomePage` 的 `permission` 徽标处可并列 engine 徽标 |

### 修订后的任务与能力矩阵

| 能力 | intellect-rag | KAG（T5 后） |
| --- | --- | --- |
| `CAP_UPLOAD` / `CAP_STRUCTURED_UPLOAD` | ✓ | ✗ |
| `CAP_SEARCH` | ✓ | ✗（P3 修订；走 kag_solve/kag_reason） |
| `CAP_DELETE` | ✓ | ✗（P1 修订；无端点） |
| `CAP_LOGS` / `CAP_PREVIEW` / `CAP_SOURCES` | ✓ | ✗ |
| `CAP_CHAT_BINDING` / `CAP_MCP_BINDING` | ✓ | ✓（委托既有实现） |

任务表相应调整：**T5.3 的 provider 只需 4 个方法**（`list_datasets` / `get_dataset` /
`create_dataset`（显式拒绝）/ `chat_binding` + `mcp_binding`），估力由 M-L 降为 **S-M**。

## 七、待评审决策点

- **D1 范围**：取方案 B（统一目录 + 分派详情）？还是 A（仅注册）／C（全量统一）？
- **D2 `detail_path_template` 归属**：后端描述符（数据驱动）还是前端映射表（分层干净）？
- **D3 列表呈现**：两引擎**混排**（带徽标）还是**分组**（"文档知识库" / "知识图谱项目"）？
  建议分组——资源模型差异大，混排易误读。
- **D4 KAG 检索试玩**：KC 详情页不承载 KAG，那 KAG 的 `search` 能力是否需要暴露给任何 UI？
  建议：本期**不暴露**（KAG 已有图浏览与 kag_solve 工具），provider 仍实现 `search`
  以备未来（成本近零）。
- **D5 设置面**：确认"链接而非合并"（§3.5）。
- **D6 是否复用 `kag_grounding_block`/`ensure_session_mcp_config` 于 provider 委托**：
  建议采纳（零重写、行为不变；避免两套 KAG 绑定逻辑并存）。
