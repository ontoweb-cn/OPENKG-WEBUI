# 知识中心 UI 对齐收尾批次：实施遗留小项（一）+ 降级中项（二）

- 日期：2026-09-26
- 状态：**评审通过（§三），实施中**
- 依据：[knowledge-center-ui-parity.md](knowledge-center-ui-parity.md) 遗留清单第一/二节；
  P0-P2 已交付。本批探针（2026-09-26，生产 ：8082 已载新代码 + 上游直探）：

| 探针 | 结果 |
| --- | --- |
| 上游 dataset 行字段 | `token_num`（非 token_count）——engine `_dataset` 读错字段，**Token 数恒 0 实为映射 bug**；`create_time`（epoch ms 串）映射正确但 UI 未格式化；行内另有 `embedding_model`/`update_time`/`graphrag_task_id` 等 |
| 真实图谱构建（联调测试库 POST index?type=graph） | 任务启动→`progress:-1.0` + `progress_msg: "Provider not found for model ."`——**上游未配置 LLM**（仅 embedding+rerank），实体抽取不可用；失败态实测 progress_msg 带 `[ERROR]` |
| `POST /datasets/{id}/chunks` | 需 `document_ids`，返回 code 0 无 data——是**批量创建**端点，不是搜索；GET 为 405。**全库分块搜索无上游支持**（语义检索 `/search` 已覆盖该需求） |
| `/api/v1/files`+`/files/move` | RagFlow **独立文件域**（"List files under a folder"），与知识库文档（dataset documents + parent_path）无涉——**KB 文档无文件夹管理/移动 API** |
| index status 任务形状 | `{begin_at, progress(-1=失败/0-1=进行), progress_msg, digest, …}`——失败态可判定（progress<0） |

## 一、任务分解

### T-A 字段映射修复 + Settings 元数据补全（0.5 人日）

- engine `_dataset`：`token_count` → 读 `token_num`（真 bug）；`KnowledgeDataset`
  增加 `embedding_model: str = ""`（行内已有，安全读取——D8 的"谨慎项"以此闭环）。
- Settings 面板：新增"嵌入模型"行；`created_at`（epoch ms 串）经 `formatDateTime`
  渲染为本地日期时间；Token 数用 formatBytes 保留。
- 验收：live 详情页 Token 数 >0、嵌入模型显示、创建时间为可读日期。

### T-B 图谱面板失败态 + 构建验收口径修订（0.5 人日）

- GraphPanel：按实测任务形状判定——`progress < 0` = 失败（**终态，停止轮询**），
  展示 progress_msg 尾部 + "重新构建"；`0 ≤ progress < 1` 轮询；有图渲染画布。
- **真实构建→画布验收被环境阻塞**（上游无 LLM，配置属用户环境决策）：defer，
  路径已文档化（在 rag-app 配置 LLM → 点击"构建图谱索引"→ 画布）。
- mind_map 渲染同因（mindmap_task 依赖 LLM）**defer**，载荷形状未知不盲写。

### T-C `?file=` 预览深链（0.5 人日）

- `api.ts` 加 `getDocument(datasetId, documentId)`（单文档端点已存在
  knowledge.py:963，response_model=KnowledgeDocument）。
- KnowledgeDetailPage：读 `?file=` 参数——先在当前页 documents 找，找不到则
  getDocument 兜底 → 打开预览抽屉。守卫：解析失败静默清除参数。

### T-D 上传逐文件进度条（1 人日）

- `api.ts`：`uploadDocuments` 改 XHR 实现（`XMLHttpRequest.upload.onprogress`），
  逐文件顺序上传并回调 `(file, percent)`——覆盖浏览器→代理段（大文件的主要耗时段）；
  代理→上游段无事件，进度到 100% 后进入"服务端处理"态。
- UploadDropzone：暂存摘要卡每行加进度条；失败行标记可重试。
- zip 与结构化上传（无单文件事件语义）保持原样，状态显示"服务端处理中"。

### T-E 文档类型图标（0.5 人日）——**替代原 thumbnails 方案**

- **修订（R2）**：内容缩略图**不做**——(a) 上游 thumbnails/images 路径无 per-user
  强制（T0 审计）；(b) 客户端 PDF 缩略图需整文件下载（10MB 级）；(c) DeepMentor
  列表用的也是**类型图标**而非内容缩略图——对齐目标本就是图标。
- 实现：`docIconFor` 式助手（扩展名→颜色徽标/图标：pdf 红、docx 蓝、md 绿、
  图片紫、代码 amber、zip 橙、其余灰），DocumentTable Name 列前置。

### T-F 两项关闭（0 落码，结论入档）

- **item 6 主从树+文件夹操作：关闭**——上游 `/api/v1/files` 域与 KB 文档域相互
  独立，dataset documents 无移动/建夹 API（DeepMentor 的树由其本地 raw/ 目录支撑，
  本仓薄代理无此数据源）。现有目录分组头已承载全部可行导航价值。
- **item 9 全库分块搜索：关闭**——上游无搜索端点（POST 为批量创建）；语义检索
  `/search`（检索试玩 + 聊天召回）已覆盖"跨文档找内容"。

## 二、横切约束（同前批）

契约：本批 router 仅 T-C 复用既有端点、无新端点——预期契约零漂移（若 getDocument
响应模型为 Any 空洞则记录不扩 scope）。i18n 字面量 + zh/en 同步。子路径规则。

## 三、技术评审记录（2026-09-26）

评审方式：对照 5 项探针实测 + 引擎/前端现状逐条核对。

### R1 thumbnails 方案修订（实质修订，采纳）

原清单第 4 项"thumbnails 接入"改为"类型图标"：三条理由见 T-E。原方案 §四 T2 的
thumbnails 引用此前已移除，本次正式关闭该缺口并在 parity 主文档遗留清单中改记。

### R2 token 计数映射为真 bug（采纳）

`_dataset` 读 `token_count` 而上游列名是 `token_num`——P0 Settings"Token 数 —"的
根因即此（非显示问题）。created_at 映射本正确，"创建时间 —"是 UI 未格式化 epoch
毫秒串之外的第二处：确认为 fetchDataset 链路在 P0 时点已带值、面板渲染原串而非"—"
——复核后以实测为准，两处（格式化 + token）一并修。

### R3 构建验收 defer 而非跳过（采纳）

上游无 LLM 是环境事实。GraphPanel 的失败终态（progress<0 停轮询 + 错误展示）用本次
真实失败任务验收——失败路径本身即是验收项；成功画布验收挂"待用户配置 LLM"。

### R4 上传进度口径（澄清）

XHR progress 只覆盖客户端→代理段；代理→上游转发无事件。UI 文案标"已上传，等待服务
端处理"，避免误导为端到端进度。

### R5 item 6/9 关闭依据充分（采纳）

两条关闭结论都有探针证据（files 域独立、POST chunks 为创建）；不属于"欠账"而属于
"上游能力边界"，在 parity 主文档遗留清单中改记为"已关闭（上游不支持）"。

### 评审结论

**通过**。R1 为方案修订（thumbnails→类型图标），R2 为 bug 定性，R3/R4 为口径，
R5 为两条关闭。实施范围 = T-A/B/C/D/E 落码 + T-F 入档。
