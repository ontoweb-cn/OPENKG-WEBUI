# 知识中心 UI 对齐 P0 细化任务清单（详情页骨架 + 文档预览 + 上传升级 + 默认库）

- 日期：2026-09-26
- 状态：**评审通过（见 §五，修订已并入正文），T1-T4 实施中**
- 依据：[knowledge-center-ui-parity.md](knowledge-center-ui-parity.md) §四 P0 + §八评审（R2/T0 前置）
- 前置：T0 鉴权审计已完成 → [../../scripts/knowledge_a0/results/t0_preview_auth_audit.md](../../scripts/knowledge_a0/results/t0_preview_auth_audit.md)

## 一、范围与不做

P0 = 方案 §四 T1-T4 五项（T0 前置已完成）。**不做**：thumbnails 接入（T0 审计结论，
预览不依赖缩略图）、文档树形列表（P1-T5）、chunk（P2-T9）、settings 改名/描述（P1-T8）。

## 二、任务分解

### T1 详情页标签页重构（约 1 人日）

**落点**：`web/features/knowledge/components/KnowledgeDetailPage.tsx`（重排）、
`web/features/knowledge/components/KnowledgePageFrame.tsx`（不动或微调）。

- 四个 tab：`documents / sources / retrieval / settings`；`sources` 内纵向保留现有
  GithubSourcePanel + WebSourcePanel（与现状行为一致，不做左右分栏）；`retrieval` =
  现 RetrievalPlayground 原样搬入；`settings` = T4 的默认知识库 + 元数据只读区。
- tab 样式对齐 DeepMentor 的下划线式（`KbFilesTab/KnowledgeBaseDetail` 的
  border-b + active 下划线），全宽；`sources` 面板限 `max-w-3xl`（对齐 DeepMentor 节奏）。
- **深链**：`?section=<tab>`（`useSearchParams` 读写；非法值回落 `documents`）。
  使用 `router.replace` 避免历史堆积。遵守子路径规则：不手拼域名前缀。
- 解析中徽标/Stop parsing/上传按钮留在页头 action 区（跨 tab 常驻）；SSE 日志面板
  与 running 轮询逻辑不动，仅随 Documents tab 展示（日志区只在 documents tab 显示）。
- **验收**：四 tab 切换无重挂载丢状态（各 tab 组件保持挂载或状态提升）；深链直达；
  现有交互（上传/删除/重解析/停止/SSE 日志）全部不回归。

### T2 文档预览抽屉（约 2 人日）

**落点**：新增 `web/features/knowledge/components/DocumentPreviewDrawer.tsx`；
`web/features/knowledge/api.ts` 加 `previewUrl(documentId)`；`model.ts` 不动。

- 交互：点击文档名打开右侧抽屉（fixed right，宽 ~min(640px, 90vw)，Esc/遮罩关闭，
  全屏切换）；表格 Name 单元格变按钮语义。
- **数据**：`GET /api/knowledge-center/documents/{docId}/preview`（代理已在，
  knowledge.py:1016）——**经 T0 审计，上游按委托用户做 per-user 可见性强制**；
  二进制响应，前端用 `apiUrl()` 拼 URL + `<img src>`/fetch blob（遵守子路径规则，
  不裸拼 `/api/...`）。
- 渲染器按扩展名（`name` 推断）`next/dynamic` 懒加载，首版四类：
  `pdf`（pdfjs-dist@6 已在）、`image`（png/jpg/jpeg/gif/webp/svg）、
  `markdown`（react-markdown@10 已在）、`text/code`（其余文本扩展白名单：md 之外的
  txt/csv/json/py/ts/tsx/js/css/html/xml/yml/yaml/log…，超出白名单显示"不支持预览"）。
- 抽屉头：文件名 + 类型徽标 + 大小 + 全屏 + 下载（下载复用 preview URL 加
  `?download=1`?——**不**：上游 preview 无 download 参数，下载用 fetch blob +
  objectURL + a[download]）；PDF 渲染上限首版 50 页（超出提示）。
- 空态/错误态：RUNNING/UNSTART 文档允许预览原始文件（preview 是原始字节，与解析无关），
  失败给内联重试。
- **验收**：四种格式 live 各验一个；403/404 显示内联错误；`?file=` 深链不做（P1-T5
  主从面板时再做，本版保持抽屉）。

### T3 拖放上传区 + 预校验摘要（约 1.5 人日）

**落点**：`KnowledgeDetailPage.tsx` Documents tab 顶部；参照 DeepMentor
`FileDropZone.tsx` 交互（不拷代码，重写为本仓四层风格）。

- 拖放区：`dragenter/over/leave` 深度计数防抖（参照其实现注释），拖入高亮；
  点击打开文件选择（multiple）。
- 预校验（纯前端，弱约束不拦截 zip/目录逻辑）：按扩展名给出
  Supported / Will skip 徽标 + 大小列表 + 单文件移除 + 清空；去重（name+size）。
  上限提示：单文件 200MB（与代理超时/上游约束对齐——实现时核对 `proxyClientMaxBodySize`
  220MB 口径后定值）。
- 仍走既有 `uploadDocuments` / `uploadStructured`（zip/文件夹分流不变）；上传中
  按钮 loading + 汇总行；**不做**逐文件进度（代理 FormData 一发式，无进度事件——记入
  P3 改造项，需要后端分块或 SSE 任务化）。
- **验收**：拖入混合文件（含不支持类型）清单正确、可移除；上传成功后列表刷新、
  RUNNING 状态照常轮询。

### T4 默认知识库 + Settings tab 基础（约 0.5 人日）

**落点**：`api.ts` 加 `fetchKnowledgePreferences/putKnowledgePreferences`；
`model.ts` 加 `KnowledgePreferences`；Settings tab 内渲染。

- 代理 `GET/PUT /preferences`（knowledge.py:217/226）已有——先读代理实现确认载荷形状
  （`default_dataset_id`?），前端按实现接，不猜字段。
- UI：Settings tab 元数据只读区（可见范围徽标、文档/分块计数、创建时间）+
  "设为默认知识库"开关（radio/checkbox 语义：默认库全局唯一，PUT 空值=清除）。
- **验收**：设置/清除默认库往返一致；刷新后状态保持。

## 三、横切约束（每个任务自查）

1. 契约：本阶段**不动 router 签名**（preview/preferences 端点已存在），
   预期 `contracts:check` 零漂移；若实现中发现响应模型为 `Any` 空洞，记录不扩 scope。
2. i18n：所有新 UI 串用 `t("…")` 字面量 + 同步补 `web/locales/zh/app.json`
   （预计 ~30 key）；`npm run i18n:check` 过。
3. 子路径规则：资源 URL 一律 `apiUrl()`；不用裸 `fetch("/api/…")`。
4. 测试：`web/tests/knowledge-parse.spec.ts` 补 preview 扩展名分流与拖放预校验的
   纯函数单测；后端无改动则不新增 pytest（request_auth 既有覆盖沿用）。

## 四、验收口径（阶段整体）

- 详情页四 tab + 深链；预览四类格式 live 各一例；拖放上传链路 live 一例；
- `npm run check:fast` 全绿 + `pytest tests/api/test_knowledge_router.py` 等既有套件全绿；
- T0 审计归档（已完成）。

## 五、技术评审记录（2026-09-26）

评审方式：对照实现落点逐任务核对（api.ts/model.ts/knowledge.py 现状、上游源码、
DeepMentor 参照组件），重点核 T0 结论对 T2 的传导、契约/i18n 横切约束的可执行性。

### R1 thumbnails 从 T2 移除（依 T0 审计，采纳）

上游 `/thumbnails` 与 `/documents/images/{id}` 均 `login_required` 而无 accessible
检查（审计 §二）。T2 只接 preview。**影响**：列表无缩略图美化（DeepMentor 也未在
列表用缩略图，无对齐损失）；P3 可再议（须代理侧先补校验）。

### R2 下载实现修正（方案文档笔误，采纳）

方案 T2 曾写"加 `?download=1`"——上游 preview 处理器无该参数（document_api.py:2117-2136
仅读 doc_id）。改为 fetch blob + `a[download]`。

### R3 T4 字段形状以代理实现为准（防猜测，采纳）

`/preferences` 载荷形状未在本清单拍死；实现第一步先读 knowledge.py:217-238 与
`test_knowledge_router.py` 既有断言定形。**已核对**：偏好以 per-user 行存储，载荷含
默认库 id（空串=清除）——实现时以路由模型为准。

### R4 上传大小上限口径（存疑待实现时定，不阻塞）

200MB 为暂定值；实现 T3 时以 `web/next.config.js` 的 `proxyClientMaxBodySize`（220MB）
与上游 upload 限制对齐取小者，并在 i18n 文案中体现。

### R5 tab 状态保持策略（采纳"保持挂载"）

四 tab 内容组件常驻挂载、以 `hidden` 切换——避免 Documents 轮询/SSE 状态在切 tab
时重建；代价是首帧多挂载两个轻面板，可接受（sources/settings 均 form 级）。

### R6 预览对 RUNNING 文档的口径（澄清）

preview 返回原始文件字节（上游直接读存储），与解析状态无关——RUNNING/FAIL/UNSTART
均可预览。T2 不做状态门控，仅失败重试。

### 评审结论

**通过**。R1/R2 为对方案文档的实质修订（thumbnails 移除、下载实现），其余为口径澄清。
按本清单实施；横切约束 §三作为每任务 DoD。
