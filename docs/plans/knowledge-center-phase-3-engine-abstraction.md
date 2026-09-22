# 知识中心 Phase 3 立项方案：多引擎接入（KnowledgeEngine provider 接口）

- 日期：2026-09-23
- 状态：**立项方案，待评审**（评审通过后按"细化任务→评审→实施"推进）
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

## 六、建议的下一步

1. **评审本文**（尤其 D1/D2/D5——它们决定是否现在动、动多大）；
2. 若通过：T1+T2 作为一个"纯重构"工作单元细化实施（不改对外行为）；
3. T3（归一化）单独评审，因其触及前端契约；
4. KAG 接入（T5）按 §十二 TODO 单独立项，以本文的抽象作为前置。
