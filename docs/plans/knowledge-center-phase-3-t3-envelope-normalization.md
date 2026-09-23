# 知识中心 Phase 3 T3 细化方案：信封归一化与域模型收口

- 日期：2026-09-23
- 状态：**细化待评审**（评审通过后实施）
- 依据：[knowledge-center-phase-3-engine-abstraction.md](knowledge-center-phase-3-engine-abstraction.md) §六评审（P1-2 的表述修正、D2 的归一化要求）+ T1/T2 实施记录
- 前置：T1+T2 已交付（`engines/{base,registry,intellect_rag}.py`，路由经 `_engine_for` 调 provider，行为不变）

## 一、为什么做（T3 的目标）

T2 是"把调用移进 provider"，但 provider 仍是**通用转发**（`request(method, path)` +
原样透传 `Response`）。后果：

1. **信封与上游字段名穿透到前端**：`{code, data, message}` 由前端 `unwrapEnvelope`
   解包；`docnm_kwd`/`content_with_weight`/`chunk_id` 等 RagFlow 专有字段由前端
   parse 层兜底（1.5 联调 R-6 的发现）——"引擎差异"实际泄漏到了浏览器；
2. **业务错误走 200**：上游 `code=109/102` 携带错误但 HTTP 200，前端靠抛
   `KnowledgeApiError` 表达失败——与项目其余部分（HTTP 状态语义）不一致；
3. **契约空洞**：OpenAPI 中 19 个 knowledge 端点全是
   `additionalProperties: true`（因为返回 `Response`），生成的前端类型无信息量；
4. **引擎切换的隐藏成本**：接 KAG 时前端要再学一套字段名。

T3 把这三层收口到 provider 内：**域模型出站、HTTP 状态表达错误、契约有实体 schema**。

## 二、域模型（`engines/models.py`，新增）

**字段命名决策 D1：保持 snake_case**——现有前端 parse 层读的就是
`document_count`/`chunk_count`/`run`/`progress` 等名称，沿用可将前端改动压到最小
（只删信封解包，不改字段读取）。

```python
class KnowledgeDataset(BaseModel):
    id: str
    name: str
    description: str = ""
    permission: str = "me"          # "me" | "team"
    document_count: int = 0
    chunk_count: int = 0
    token_count: int = 0
    created_at: str = ""

class DatasetPage(BaseModel):
    datasets: list[KnowledgeDataset]
    total: int = 0

class KnowledgeDocument(BaseModel):
    id: str
    name: str
    run: Literal["UNSTART", "RUNNING", "DONE", "FAIL", "CANCEL"] = "UNSTART"
    progress: float = 0.0           # 0-100
    chunk_count: int = 0
    token_count: int = 0
    size: int = 0
    location: str = ""

class DocumentPage(BaseModel):
    documents: list[KnowledgeDocument]
    total: int = 0

class SearchChunk(BaseModel):
    id: str
    content: str
    similarity: float = 0.0
    document_name: str = ""

class SearchResult(BaseModel):
    chunks: list[SearchChunk]
    total: int = 0
    #: 被权限拒绝的库 id（上游数据级信号）——前端当前未消费，先纳入契约备未来使用
    denied_dataset_ids: list[str] = []

class IngestionLog(BaseModel):
    id: str
    progress: float = 0.0
    message: str = ""               # ← 上游 progress_msg
    status: str = ""
    document_name: str = ""

class UploadResult(BaseModel):
    uploaded: int = 0
    documents: list[KnowledgeDocument] = []

class StructuredUploadResult(BaseModel):
    """结构化上传按目录分组的结果（替代 `[{directory, status, upstream}]`）。"""
    directory: str
    uploaded: int = 0
    error: str = ""

class BinaryPayload(BaseModel):
    """二进制透传（预览/缩略图）——不进 JSON 契约，仅内部载体。"""
    content: bytes
    media_type: str
```

## 三、错误模型（`EngineError` 扩展 + 映射表）

现状 `EngineError(kind, message)` 只有两种 kind。T3 扩展为携带上游信号，路由层
统一映射（**取代 `_service_error` 的临时映射**）：

| 上游信号 | kind | HTTP | 说明 |
| --- | --- | --- | --- |
| 传输失败 / 超时 | `unreachable` | **502** | 现 `knowledge_upstream_unreachable` |
| HTTP 5xx | `upstream_error` | **502** | 上游故障 |
| HTTP 401/403 或 envelope `code=109` | `unauthorized` | **403** | 权限/身份被拒（前端提示无权限） |
| HTTP 404 或 envelope `code=102` 且 message 含 not found | `not_found` | **404** | 库/文档不存在 |
| HTTP 4xx 其他 | `invalid` | **400** | 参数/请求错误 |
| envelope `code≠0` 其他 | `upstream_error` | **502** | 保留 message 原文供 UI 展示 |

配置/身份层错误（`KnowledgeNotConfigured`/`KnowledgeIdentityUnavailable`）保持
**409**（设置页引导语义，1a 既有约定）。

路由层单一入口：

```python
def _http_error(exc: Exception) -> HTTPException:   # 取代 _service_error
    # 409 配置/身份 → 403 unauthorized → 404 not_found → 400 invalid → 502 其余
```

## 四、Provider 方法（15 个，替代 `request()`）

T2 的 `request(method, path)` 是过渡态；T3 用类型化方法，路径知识收回 provider
内部（**同时删掉路由里的 `_dataset_id_from_path` 路径启发式**）。

| # | 方法 | 对应端点 |
| --- | --- | --- |
| 1 | `list_datasets(page, page_size) -> DatasetPage` | GET /datasets |
| 2 | `get_dataset(id) -> KnowledgeDataset` | GET /datasets/{id} |
| 3 | `create_dataset(name, description, visibility) -> KnowledgeDataset` | POST /datasets |
| 4 | `delete_dataset(id) -> None` | DELETE /datasets/{id} |
| 5 | `list_documents(dataset_id, page, page_size) -> DocumentPage` | GET …/documents |
| 6 | `get_document(dataset_id, document_id) -> KnowledgeDocument` | GET …/documents/{doc} |
| 7 | `upload(dataset_id, sources, parent_path) -> UploadResult` | POST …/documents |
| 8 | `delete_documents(dataset_id, ids) -> None` | DELETE …/documents |
| 9 | `parse_documents(dataset_id, ids) -> None` | POST …/documents/parse |
| 10 | `stop_parsing(dataset_id, ids) -> None` | POST …/documents/stop |
| 11 | `search(dataset_id, question, options) -> SearchResult` | POST …/search |
| 12 | `list_ingestions(dataset_id, log_type, page) -> list[IngestionLog]` | GET …/ingestions |
| 13 | `get_ingestion(dataset_id, log_id) -> IngestionLog` | GET …/ingestions/{log} |
| 14 | `preview_document(document_id) -> BinaryPayload` | GET /documents/{id}/preview |
| 15 | `thumbnail(document_id) -> BinaryPayload` | GET /thumbnails |

**流式契约（决策 D4，最高风险项）**：上传**必须保持流式**——现状路由传
`UploadFile.file`（SpooledTemporaryFile）由 httpx 分块读取，是 1a 的显式设计
（"不整体载入内存"，任务清单 §七）。因此：

```python
UploadSource = Union[bytes, BinaryIO]   # bytes（zip 展开/同步源）或文件对象（直传）
```
provider 直接把 source 交给 httpx（它同时支持两者），**禁止 `.read()` 收口为 bytes**；
回归测试需断言大文件路径未 buffer（以 `httpx` 收到 file-like 为准）。

**辅助**：`health() -> EngineHealth`（探测用，可选，T3 不强制）。

## 五、路由层变化

- 20 个引擎型端点：`await _upstream(...)` → `await engine.<方法>(...)`，返回值
  由 FastAPI 直接序列化（pydantic）；
- **状态端点**（GET ""）、**A3 偏好**（GET/PUT /preferences）、**外部源配置**
  （PUT/GET/DELETE /sources/*）保持路由本地（不属引擎域）；
- **结构化上传**：zip 解包/目录分组保留在路由（跨引擎的通用逻辑），每组调
  `engine.upload()`，聚合为 `list[StructuredUploadResult]`；
- **同步任务**（GitHub/Web）：改用类型化方法（`upload`/`list_documents`/
  `delete_documents`），不再有裸路径；
- **SSE 日志流**：已是域形状 `{"logs": [...]}`，改为 `list[IngestionLog]` 序列化；
- **新建库要 pin 引擎**（T1/T2 遗留）：`create_dataset` 成功后调
  `registry.pin_dataset_engine(dataset_id, engine.engine_id)`（单引擎下行为中性，
  但是多引擎路由的地基）；`delete_dataset` 调 `unpin_dataset_engine`。
- 删除 `_upstream`、`_dataset_id_from_path`、`_service_error`、`_UPSTREAM_TIMEOUT`
  等 T2 过渡件。

## 六、前端变化（**改动面比预期小**）

关键事实（已核实）：

| 事实 | 影响 |
| --- | --- |
| `KnowledgeApiError` **零消费者**（组件从不 catch 它） | 删除它几乎零成本 |
| 组件只消费域模型（`KnowledgeDataset` 等），不碰信封 | 1536 行组件**零改动** |
| `parse*` 函数读的字段名（`document_count`/`chunk_count`/`run`/`progress`）与 T3 域模型一致（D1） | parse 层只需去掉 `.data` 解包 |
| `denied_dataset_ids` 前端未消费 | 纳入契约即可，无 UI 改动 |
| 错误渲染走 `err.message`（`ApiError` 兼容） | 错误语义变化不需 UI 改动 |

具体：

1. `web/features/knowledge/api.ts`：删 `unwrapEnvelope`、`KnowledgeApiError`、
   `requestKnowledge`（约 35 行）；各函数直接 `requestJson` 并交给 parse 层；
2. `web/features/knowledge/model.ts`：6 个 `parse*` 去掉 `record(payload).data`
   解包（改为直接读 payload），保留容错字段读取（后端已保证形状，容错是廉价保险）；
3. 契约再生成：`export_frontend_contracts.py` + `contracts:generate`——19 个端点从
   `additionalProperties: true` 变为实体 schema，生成类型可供未来使用（本轮不强制
   前端改用生成类型：手写域模型 + 容错解析保留，生成类型进 CI 契约门禁）。

## 七、任务拆分（评审通过后实施）

| # | 任务 | 说明 | 估力 |
| --- | --- | --- | --- |
| T3.1 | 域模型 + 错误模型扩展 | `engines/models.py`、`EngineError(kind, message, upstream_code?, upstream_status?)`、`_http_error` 映射表 | S-M |
| T3.2 | provider 15 方法 | 含流式上传（D4）、二进制端点、envelope→域模型转换、envelope code→EngineError 归一 | M-L |
| T3.3 | 路由重写 20 端点 + pin/unpin 接线 | 删 T2 过渡件；结构化上传聚合域形状 | M-L |
| T3.4 | 后端测试重写 | 现有 22 项断言（信封 → 域形状/状态码）+ 新增错误映射 6 例 + 流式 1 例 | M |
| T3.5 | 前端简化 | api.ts/model.ts；可选补 4 个 parse 函数的 vitest（当前零覆盖） | S-M |
| T3.6 | 契约再生成 + live 验收 | 契约双侧 check；生产实例复验列表/检索/上传/删除/错误码 | S |

提交切分：T3.1+T3.2（provider 就绪，路由未动 → 编译通过但未使用）→ T3.3+T3.4
（原子切换）→ T3.5+T3.6（前端与契约）。

## 八、验收标准

1. **对外行为**：列表/检索/上传/删除/预览 5 条链路与 T3 前一致（live 复验）；
2. **错误语义**：访问无权库 → 403（此前 200+code109）；不存在库 → 404；
   上游不可达 → 502；未配置 → 409；
3. **流式**：直传上传仍不 buffer（测试断言 httpx 收到 file-like）；
4. **契约**：19 端点有实体 schema；`contracts:check` 与前端门禁通过；
5. **回归**：后端 487 项（重写后数量持平或增加）+ 引擎单测 10 项 + 前端门禁全绿；
6. 组件层零改动（1536 行 diff 为空）。

## 九、风险

| 风险 | 缓解 |
| --- | --- |
| **流式上传退化为内存 buffer**（最高） | D4 契约 + 专项测试断言 source 为 file-like；代码评审禁止 `.read()` 收口 |
| 22 项测试断言重写引入误判 | 按"信封 → 域形状"逐项对照改写，保留原断言语义（如 same-origin/403 用例不动） |
| 二进制端点 content-type 丢失 | `BinaryPayload.media_type` 透传 + preview/thumbnail live 验证 |
| 错误语义变化影响 UI 文案 | 映射表保留上游 message 原文；错误渲染已走 `err.message` |
| 前端 parse 简化引入字段漂移 | 可选补 vitest（4 函数）；live 复验兜底 |
| 契约再生成引入无关 diff | 只重生成 knowledge 相关；`contracts:check` 验证无漂移 |

## 十一、技术评审记录（2026-09-23）

**总评：方向与拆分成立；错误映射表有实质缺陷（P1）必须修订，流式契约表述需精确化（P2），其余论断经核实全部属实。**

### P1（必须修订）错误映射表不完整且低估了 102 的重载

评审核对了上游 `common/constants.py` 的 RetCode 全集，方案 §三 的表格**漏了三个码**，
且对 102 的判断有误：

| RetCode | 值 | 语义 | 方案缺失 | 应映射 |
| --- | --- | --- | --- | --- |
| SUCCESS | 0 | 成功 | — | — |
| ARGUMENT_ERROR | 101 | 参数错误 | **漏** | 400 |
| DATA_ERROR | 102 | **重载**（见下） | 误判 | 按 message 细分 |
| OPERATING_ERROR | 103 | 操作失败 | **漏** | 400/502 |
| CONNECTION_ERROR | 105 | 上游连接失败 | **漏** | 502 |
| PERMISSION_ERROR | **108** | 权限拒绝 | **漏（关键）** | **403** |
| AUTHENTICATION_ERROR | 109 | 认证失败 | ✓ 已列 | 403 |
| EXCEPTION_ERROR | 100 | 未捕获异常 | **漏** | 502 |

**102 的重载证据**（这是 D2 的真正答案）——实测该码同时承载三类语义：
- `"No authorization."`、`"User 'X' lacks permission for dataset 'Y'"`
  （`dataset_api_service.py:211/241/276`）→ **权限语义，应 403**；
- `"Document not found!"`、`"Artifact not found."`、`"Image not found."`
  → **存在性语义，应 404**；
- `"Invalid filename."`、`"Invalid file type."`、`"Not supported yet!"`
  → **参数语义，应 400**。

**修订后的 102 判定规则**（message 关键词，优先级从高到低）：
```
"not found" / "not exist"        → 404 not_found
"permission" / "no authorization" → 403 unauthorized
"invalid" / "not supported" / "not allowed" → 400 invalid
其他                              → 400 invalid（保留原文 message）
```
（注意：不能笼统把 102 当 404——权限拒绝走 404 会让 UI 误报"不存在"。）

### P2（必须修订）D4 流式契约的表述过宽，会与现状冲突

方案 §四 把"上传必须保持流式"写成上传的通用契约，但实测**两条路径不同**：

| 路径 | 现状 | 结论 |
| --- | --- | --- |
| 直传多文件（`POST …/documents`） | 路由传 `f.file`（SpooledTemporaryFile，file-like）→ httpx 分块读 | **需保持流式**（1a 设计意图） |
| 结构化上传（`…/documents/structured`） | 路由 `await f.read()` 收口 bytes（`knowledge.py:404`） | **已经 buffer，且必然如此**——zip 需全量字节解包、目录分组需先成形 |

**修订**：D4 改为"**直传路径**保持流式（`UploadSource` 允许 `BinaryIO`，provider 不得
`.read()`）；结构化路径按设计显式 buffer（已在 Phase 2 的安全上限内），验收标准只覆盖
直传路径"。否则按方案字面，结构化路径会被判"违反契约"。

### P3 其余核实结论（方案论断成立）

| 方案论断 | 核实结果 |
| --- | --- |
| `KnowledgeApiError` 零消费者 | ✓ 属实（全仓除定义处无引用）→ 删除零成本 |
| 组件只消费域模型字段 | ✓ 属实（抽查 `KnowledgeHomePage`：`.documentCount/.chunkCount/.permission/.name/.id`） |
| parse 读的字段名与 T3 域模型一致 | ✓ 属实（`document_count/chunk_count/run/progress` 等，D1 的 snake_case 选择正确） |
| 19 端点契约空洞（`additionalProperties: true`） | ✓ 属实 |
| `denied_dataset_ids` 前端未消费 | ✓ 属实（前后端均无引用）→ 纳入契约备未来使用合理 |
| T2 过渡件（`_upstream`/`_dataset_id_from_path`/`_service_error`）可删 | ✓ 属实，且删 `_dataset_id_from_path` 能顺带消除路径启发式的脆弱性 |

### P4 补充建议（非阻塞）

1. **`EngineError` 应携带上游原始信号**（`upstream_code`、`upstream_status`、`message`），
   让映射逻辑单点可测——方案 §三 只说扩展 kind，未规定字段；建议：
   `EngineError(kind, message, *, upstream_code: int | None = None, upstream_status: int | None = None)`；
2. **HTTP 状态与 envelope 的优先级**：当上游 HTTP 与 envelope 同时异常（如 HTTP 200 +
   code=109），以 envelope 为准（现状如此）；当 HTTP 5xx 且 envelope 缺失，用 status；
   建议在映射函数注释中写明该优先级并加参数化测试；
3. **契约门禁**：19 端点从 `additionalProperties` 变为实体后，`contracts:check` 会产出
   大 diff——建议 T3.6 单独提交契约产物，避免与逻辑 diff 混淆；
4. **T3.5 可选 vitest**：当前 `features/knowledge/` 前端零单测，4 个 parse 函数的简化
   建议补 vitest（对实现是回归保护，对后续 KAG 接入是形状基准）。

### 确认项（评审通过）

- **D1 snake_case**：正确（前端零字段改动，只删信封解包）；
- **D3 不做双形状兼容**：可采纳（单仓库同版本部署 + `KnowledgeApiError` 无消费者，
  过渡形状没有受益方）；
- **D5 前端不改用生成类型**：可采纳（手写域模型 + 容错解析保留，生成类型仅进 CI）；
- **D6 SSE 一并类型化**：可采纳（已是域形状，成本近零）；
- **任务拆分与提交切分**（T3.1+T3.2 → T3.3+T3.4 原子切换 → T3.5+T3.6）：合理，
  原子切换是关键（避免中途前后端形状不一致）。

## 十二、补充评审（第二轮，2026-09-23）

首轮评审（§十一）聚焦错误映射与流式契约。第二轮逐条核对端点与实现细节，新增
两项必须修订的问题。

### P1（必须修订）同步任务的错误语义会回归——须规定"逐项容错"

**现状语义**：GitHub/Web 同步任务用 `if resp.status_code < 400: uploaded += 1`
（`knowledge.py:548/579/723`）——**单个文件失败不中断同步**，计数跳过、循环继续。

**T3 后的危险**：provider 方法返回域模型、失败抛 `EngineError`。若按方案 §五 的
"改用类型化方法"字面实施，循环体里一次失败即**抛出并中断整个同步**——从"逐项容错"
退化为"首错即停"。外部源同步正是最容易出现单文件失败（远端 404/格式不支持）的场景。

**修订要求**（写入 T3.3）：
- 上传循环：`try: await engine.upload(…) except EngineError as exc: failures.append(…)`
  ——逐项捕获、继续后续项；结束后把 `uploaded/failed` 都写进同步状态
  （`last_result` 现为 `"uploaded N, removed M"`，建议扩为含 `failed K`）；
- 列表/删除同理：列表失败可整体视为本次同步失败（保持现状），删除失败逐项容错；
- **验收补一条**：构造"部分文件上传失败"场景（如计划中含 1 个必然失败项），断言
  其余文件仍成功入库。

### P2（必须补充）两处 `total` 来源不同——`DatasetPage.total` 需写明取值点

实测（2026-09-23）：

| 端点 | `total` 位置 | 形状 |
| --- | --- | --- |
| `GET /datasets` | **信封顶层** `total_datasets` | `{code, data: [...], total_datasets: N}` |
| `GET /datasets/{id}/documents` | **`data` 内** `data.total` | `{code, data: {docs: [...], total: N}}` |

方案 §二 定义了 `DatasetPage(datasets, total)` 与 `DocumentPage(documents, total)`，
但未写明各自 `total` 从哪取。实现者若按 docs 的模式找 `data.total` 会恒得 0
（datasets 的 data 是**裸列表**）。

**修订要求**：§二 的模型旁注明取值点；并借此关闭 1a 评审的遗留 **R-4**
（"GET /datasets 的 total 在信封顶层，unwrap 时被丢弃——分页需透传"）：
T3 后 `DatasetPage.total` 即该字段的归宿，前端分页可用。

### P3（文档准确性，非阻塞）

方案 §五 称"20 个引擎型端点"，实际核对为 **15 个端点**对应 15 个 provider 方法
（另 3 个走 provider 但端点为路由本地：结构化上传、SSE 日志流、两个 sync 触发）。
总端点 25 个。建议改为"15 个引擎型端点 + 3 个复合端点"以免实现时误期。

### 复核确认（第二轮）

- 15 个 provider 方法与端点**一一对应**，无遗漏无多余（逐条比对 `@router` 清单）；
- 路由本地端点（status/preferences/sources 配置与触发）**与引擎域正交**，不进 provider 正确；
- documents 上游形状 `data.docs` + `data.total` 与 `parseKnowledgeDocuments` 一致 ✓；
- datasets 上游 `data` 为裸列表 + 顶层 `total_datasets` ✓（前端 `parseKnowledgeDatasets`
  的 `Array.isArray(data) ? data : payload` 双分支正是为该形状写的兜底）。

## 十、决策记录（2026-09-23 定稿）

| # | 决策点 | 决策 |
| --- | --- | --- |
| D1 | 字段命名 snake_case | **采纳**（前端零字段改动，仅删信封解包） |
| D2 | 错误映射表 | **按评审修订版采纳**：以 RetCode 全集为准（含 108/105/101/100），102 重载码按 message 关键词细分（`not found`→404 / `permission`·`no authorization`→403 / `invalid`·`not supported`→400 / 其他→400） |
| D3 | 不做双形状兼容 | **采纳**（单仓库同版本部署 + `KnowledgeApiError` 无消费者） |
| D4 | 流式契约 | **采纳修订版**：仅**直传路径**保持流式（`UploadSource` 允许 BinaryIO）；结构化上传按设计显式 buffer |
| D5 | 前端不用生成类型 | **采纳**（手写域模型 + 容错解析；生成类型仅进 CI 契约门禁） |
| D6 | SSE 端点一并域化 | **采纳** |

评审 P1（逐项容错）与 P2（total 取值点 + 关闭 R-4）纳入 T3.3 实施范围。

## 十、待评审决策点

- **D1 字段命名 snake_case**（沿用现有前端读取，最小改动）——建议采纳；
- **D2 错误映射表**（§三）——需确认 403/404/400/502 的划分，特别是
  `code=102`（上游既用于 not found 也用于参数错误）是否按 message 关键词细分，
  或统一 400；
- **D3 不做双形状兼容**（单一仓库同版本部署；`KnowledgeApiError` 无消费者）——
  建议采纳；若要保守可保留 parse 层的"信封感知"过渡一版；
- **D4 流式契约**（§四）——建议采纳（否则是明确回归）；
- **D5 前端是否改用生成类型**——建议本轮不改（保留手写域模型 + 容错），
  生成类型仅进 CI 契约门禁；
- **D6 SSE 端点是否也域化**（当前已是域形状，仅类型化）——建议一并做，成本近零。
