# 知识中心 Phase 3 立项方案：多引擎接入（KnowledgeEngine provider 接口）

- 日期：2026-09-23
- 状态：**T1+T2 已实施（2026-09-23）；T3+ 待评审**
- 实施记录：T1（协议/注册表/CAP_* 常量/EngineError/EngineContext）与 T2
  （intellect-rag provider + 路由与同步任务全部经引擎）已落地——
  纯重构、对外行为不变：后端 487 项 + 引擎单测 10 项全过，live 复验
  列表/检索/上传三条链路一致，depcruise 无分层违规。
  代码落点：`openkg_webui/services/knowledge/engines/{base,registry,intellect_rag}.py`；
  路由改造见 `api/routers/knowledge.py`（`_engine_for` + `_upstream`）。（评审通过后按"细化任务→评审→实施"推进）
- 依据：[../knowledge-center-port-design.md](../knowledge-center-port-design.md) §三引擎抽象、§六 Phase 3
- 前置：Phase 1a/1b/1.5/2 已交付；当前为**单引擎硬编码**（`services/knowledge/` 直连 intellect-rag）

## 一、现状与差距

**现状**：`openkg_webui/services/knowledge/__init__.py` 直接实现 intellect-rag 语义——
`resolve_upstream_connection()` 读 settings 的 `base_url/api_key`，代理路由把
`/api/knowledge-center/*` 逐端点镜像到 rag-app 的 `/api/v1/*`。**没有任何引擎抽象**：
接口形状、鉴权方式、字段命名都写死在调用点。

**目标（设计 §三）**：KB 从第一天带 `engine` 字段；引擎目录可注册；加引擎 =
backend 加 provider + 目录加条目，**数据无需迁移**、知识中心核心不改。

## 二、接口形状建议（评审重点）

核心判断：引擎差异有三层，抽象必须分层处理，否则接口会泄漏。

```python
# services/knowledge/engines/base.py（新增）

class KnowledgeEngine(Protocol):
    """知识引擎 provider 接口（Phase 3）。

    设计原则（评审要点）：
    1. 接口只暴露知识中心**实际消费**的能力（列表/文档/上传/检索/删除），
       不做"引擎能力全集"——未来引擎能力差异由可选能力探测表达；
    2. 路由层不再镜像上游路径，改为调用 provider 方法（代理→编排的转变）；
    3. 响应统一为 knowledge 域模型（`KnowledgeDataset/Document/...`），
       引擎字段映射在 provider 内完成——前端不再感知上游命名。
    """

    engine_id: str                      # "intellect-rag" | "kag" | ...
    display_name: str
    capabilities: frozenset[str]        # {"upload", "search", "delete", ...}

    async def health(self) -> EngineHealth: ...
    async def list_datasets(self, *, user_id: str | None) -> list[KnowledgeDataset]: ...
    async def create_dataset(self, *, name: str, description: str,
                             visibility: str) -> KnowledgeDataset: ...
    async def delete_dataset(self, dataset_id: str) -> None: ...
    async def list_documents(self, dataset_id: str) -> list[KnowledgeDocument]: ...
    async def upload(self, dataset_id: str, files: list[UploadItem],
                     parent_path: str = "") -> UploadResult: ...
    async def delete_documents(self, dataset_id: str, ids: list[str]) -> None: ...
    async def search(self, dataset_id: str, question: str,
                     **options) -> list[SearchChunk]: ...
```

**注册表**（轻量，不引入 entry-point 机制——当前只有 2 个引擎候选）：

```python
# services/knowledge/engines/registry.py
_ENGINES: dict[str, KnowledgeEngine] = {}

def register(engine: KnowledgeEngine) -> None: ...
def get(engine_id: str) -> KnowledgeEngine: ...   # 未知 id → 明确报错
def list_engines() -> list[EngineDescriptor]: ...  # 供 UI 目录（名称/图标/描述/能力）
```

**路由变化**：`/api/knowledge-center/*` 保持路径与响应形状不变（前端零改动），
内部由 `router → 引擎镜像` 改为 `router → registry.get(engine).method()`。
`engine` 从 KB 元数据解析（列表/详情返回 `engine_id`），或请求参数指定（创建时）。

## 三、关键决策点

| # | 决策 | 建议 |
| --- | --- | --- |
| **D1** | **抽象时机与范围** | 建议**先抽象接口、只接一个 provider（现状 intellect-rag）**——纯重构、行为不变，用回归测试锁定；第二个引擎落地时只加 provider 实现。理由：接口形状只有在有第二个实现时才真正验证，但提前抽象能避免代理层继续泄漏上游语义（如 `denied_dataset_ids`、`{code,data,message}` 信封） |
| **D2** | **信封与字段命名归属** | 建议**provider 内归一**：路由层不再透传 `{code, data, message}`，改为业务异常（`EngineError`）→ HTTP 状态码；前端 parse 层的上游字段兼容（`chunk_id`/`content_with_weight` 等）上移到 provider。**代价**：Phase 1a/1.5 的前端 parse 函数需简化（有回归测试保护） |
| **D3** | **KAG 作为第二引擎的接入方式** | 设计 §十二 TODO 已立项：KAG 数据在 OpenSPG、与 intellect-rag 异构（图推理 ≠ 向量检索）。建议 KAG 的 provider 走 **openkg-webui 侧**（`services/kag/` 已有客户端与管理面），而非 rag-app facade——这样"加引擎"的两条路（外部引擎 facade / 本地引擎 provider）都被验证 |
| **D4** | **能力差异表达** | 用 `capabilities` 集合而非继承层次：UI 按能力显示/隐藏操作（如 KAG 无"上传文档"）。**不做**运行时能力协商的复杂机制 |
| **D5** | **KB 的 engine 字段落点** | 存放位置取决于引擎：intellect-rag 的 KB 权威数据在 rag-app（不能用本地字段覆盖），因此 `engine_id` 对 intellect-rag 恒为默认值、仅在本地映射或 settings 层记录；KAG 侧 KB 由 openkg-webui 拥有，可存本地。**结论**：`engine_id` 是"路由提示"而非权威字段，解析规则需在方案中写明 |

## 四、任务清单（评审通过后细化）

| # | 任务 | 说明 |
| --- | --- | --- |
| T1 | 定义 `KnowledgeEngine` 协议 + 域模型 + 注册表 + 模块骨架 | 纯新增，无行为变更 |
| T2 | 把现有 intellect-rag 逻辑抽为 `IntellectRagEngine` provider | 纯重构：路由改调 provider；**回归测试必须全绿**（现有 487 项 + 前端门禁） |
| T3 | 归一化收口（D2）：信封→异常、字段映射上移 | 需同步简化前端 parse；有回归保护 |
| T4 | UI 引擎目录（D4）：`/knowledge-center` 显示 engine 徽标；创建时（未来）可选引擎 | 当前单引擎时退化显示 |
| T5 | 第二引擎接入（KAG，D3）——**独立立项**，依赖 §十二 TODO | 验证抽象是否成立 |

## 五、风险

- **过度抽象**：只有一个实现时接口容易设计过宽/过窄 → 缓解：D1 的分阶段（先重构、后验证）+ 明确"只暴露实际消费的能力"。
- **回归面大**：T2/T3 触及全部知识路由 → 缓解：现有回归测试（后端 487 + 前端 5 项安全用例 + live 链路）作为门禁，分两次提交（先纯重构、再归一化）。
- **D2 的兼容成本**：前端 parse 层简化会触及 1a/1.5 的既有代码 → 若评审倾向保守，可保留 parse 层的宽兼容（既接受信封也接受归一结果），代价是过渡期两套形状并存。
- **KAG 异构性**：KAG 无"文档上传"概念（图谱构建语义不同）→ 这正是 D4 能力集合要表达的差异，但需在 KAG provider 设计中细化。

## 六、技术评审记录（2026-09-23）

**总评：分层方向正确（D1/D3/D4 成立），但抽象范围漏了两大面、且内部有自相矛盾之处。修订后可作为 T1/T2 的依据。**

### P1-1 抽象范围只覆盖管理面，遗漏「聊天面」与「MCP 面」

方案 §二 的接口只有管理面方法（datasets/documents/upload/search），但知识引擎在
本系统有**三条接入面**，另两条同样高度引擎耦合：

| 面 | 现状落点 | 引擎耦合点 |
| --- | --- | --- |
| 管理面 | `api/routers/knowledge.py`（28 端点） | 镜像 rag-app `/api/v1/*`（方案已覆盖） |
| **聊天面** | `services/knowledge/__init__.py:195` `chat_rag_block()` + `agent_loop/http_backend.py:695-700` | 返回 `{"enabled":true,"scope":chat_scope}`——这是 **intellect-team 网关 `build_session_config` 契约**（runs payload 的 `rag` 块），不是通用形状 |
| **MCP 面** | `services/knowledge/__init__.py:111-170` `ensure_knowledge_mcp_config()` | 写死 intellect-rag MCP server 的 URL/工具名（`intellect_retrieval`）+ 鉴权头 |

KAG 的第二引擎形态与此**完全不同**：KAG 走 grounding 块 + kag-bridge MCP
（见 kag-integration-design），没有 runs `rag` 块。若抽象不含这两面，接入 KAG 时
`chat_rag_block()`/`ensure_knowledge_mcp_config()` 会变成 `if engine == ...` 分支，
抽象即失效。

**修订建议**：接口补两个方法（或声明为可选能力）：
```python
async def chat_binding(self, kb_ids: list[str]) -> dict | None:
    """本 turn 的聊天侧绑定产物（intellect-rag: runs rag 块；
    KAG: grounding 块片段 / None）。None = 该引擎不参与聊天侧。"""
async def mcp_binding(self, workdir: str) -> None:
    """会话 MCP 注入（intellect-rag: .mcp.json intellect-knowledge 条目；
    KAG: kag-bridge 条目 / 不注入）。"""
```
并把 `chat_scope` 从全局 settings 移入引擎配置（它是网关专有语义）。

### P1-2 内部矛盾：§二"前端零改动" vs D2"信封归一化"

§二 声称"路径与响应形状不变（前端零改动）"，D2 又要求"路由层不再透传
`{code, data, message}`，改为业务异常"。两者冲突——前端 `unwrapEnvelope`
（`web/features/knowledge/api.ts`）**正是按信封解包的**：去掉信封 = 前端
parse 层必改（方案在 D2 也承认"前端 parse 函数需简化"，与 §二 的表述打架）。

**修订建议**：§二 改为"路径不变；**响应形状在 T3 归一化时变更，前端 parse 层
同步简化**"，并在任务表标注前端改动量（不是零）。

### P2-1 sources 子系统未纳入

`_run_github_sync` / `_run_web_sync`（`api/routers/knowledge.py:480-540`）自行拼
`f"{base_url}/api/v1"` 并 POST `/datasets/{id}/documents`——**绕过 provider 直连
引擎 REST**。抽象后必须改走 `provider.upload()`，否则引擎切换时外部源同步
静默打到旧引擎。任务表需补此项（T2 范围内）。

### P2-2 D5 未给出可执行的 engine 解析规则

"`engine_id` 是路由提示而非权威字段" 方向正确（intellect-rag 的 KB 权威在
rag-app，无法覆盖），但**没有说清 存储与解析规则**：现有 KB（Phase 1 建的）
没有 engine 记录，请求 `dataset_id` 如何知道路由到哪个 provider？

**修订建议**（择一，建议 a）：
- **(a) 本地映射表**：`<user_data_dir>/knowledge_engines.json`（`dataset_id →
  engine_id`），创建时写入；未命中 → 默认引擎（`settings.knowledge.engine`，
  缺省 `intellect-rag`）。与 A3 默认库、sources store 同构，纯本地、零引擎改动；
- (b) dataset_id 命名空间前缀（`kag:<id>`）——侵入 id 语义，不建议。

### P2-3 接口未定义身份上下文传递方式

方法签名不一致：`list_datasets(*, user_id)` 有身份，`create_dataset/upload/search`
没有——但鉴权是**每请求**解析的（现状 `_upstream()` 每请求取
`resolve_request_auth()`，P1-1 要求同一身份源）。KAG 的身份又是 OpenSPG
`user_no`（另一套）。

**修订建议**：provider **按请求实例化**，上下文在构造时绑定：
```python
class EngineContext:  # user_id / language / request_scope / identity headers
    ...
def build_engine(engine_id: str, ctx: EngineContext) -> KnowledgeEngine: ...
```
方法签名不再各自带 user_id（消除不一致，也让 provider 能在内部复用同一份鉴权）。

### P3 修订项

- **capabilities 取值未定义**：`frozenset[str]` 需要枚举常量（如
  `CAP_UPLOAD/CAP_SOURCES/CAP_LOGS/CAP_GRAPH`），否则 UI 无法 switch；
- **错误模型未细化**：`EngineError` 需带 `kind`（unreachable/unauthorized/
  not_found/upstream_error）→ HTTP 映射表；现状 502 归一与 `denied_dataset_ids`
  的数据级信号要有归宿（建议 `SearchResult{chunks, denied_dataset_ids, total}`）；
- **分页/流式/日志流未体现**：rag-app 支持 page/page_size 而 KAG 未必 →
  建议 `Page[T]` 显式（或声明"引擎可按能力忽略分页"）；`upload` 必须写明
  **流式契约**（现状代理刻意不 buffer 大文件，是 1a 的风险注记点）；
  `logs/stream`（SSE 日志）如何抽象需表态（建议 `capabilities` 标注 +
  可选方法 `stream_logs()`）；
- **模块布局未规划**：`services/knowledge/__init__.py`（247 行）当前同时承载
  settings 访问、身份解析、MCP 注入、A3 偏好、chat_rag_block——providers 移入
  `engines/` 后这些函数需明确归属，否则会形成 `routers → engines → services`
  的环。建议：`engines/`（纯 provider）+ `context.py`（EngineContext/身份）+
  `bindings.py`（chat/MCP），`__init__.py` 仅做导出；
- **测试基线引用不准**：方案写"现有 487 项"，实际是**全套**后端测试数；
  知识域专项是 `tests/api/test_knowledge_router.py`（23 项）+ agent_loop/config
  相关——T2 的门禁应点名这个子集，而非笼统"487 全绿"（后者含与本重构无关的
  大量用例，且全套偶发 OOM）。

### 确认项（评审通过）

- **D1 分阶段（先纯重构、只接一个 provider）**：判断正确且诚实——
  "接口形状只有在有第二个实现时才真正验证"，用重构 + 回归锁定风险是小步策略；
- **D2 的核心判断（信封与上游字段命名是真实耦合）**：证据充分（前端
  `unwrapEnvelope` + `chunk_id/content_with_weight` 等字段映射确实是
  rag-app 专有），仅需修正 §二 的矛盾表述；
- **D3（KAG 走 openkg-webui 侧 provider）**：与 kag-integration-design 的
  "引擎维护自己的数据权威" 一致，且能让两条引擎接入路径都被验证；
- **D4（能力集合优于继承）**：当前规模下正确；仅需补取值定义。

## 六、建议的下一步

1. **评审本文**（尤其 D1/D2/D5——它们决定是否现在动、动多大）；
2. 若通过：T1+T2 作为一个"纯重构"工作单元细化实施（不改对外行为）；
3. T3（归一化）单独评审，因其触及前端契约；
4. KAG 接入（T5）按 §十二 TODO 单独立项，以本文的抽象作为前置。
