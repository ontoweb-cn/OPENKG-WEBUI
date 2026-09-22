# 知识中心 Phase 2 实施任务清单（评审稿）

> **执行状态（2026-09-22，评审后实施完成）**
>
> - T1 ✅ 结构化上传代理：`POST /datasets/{id}/documents/structured`（zip 安全
>   解包：zip-slip/条目/体积熔断；`rel_paths` 保留目录；按目录分组转发）。
> - T2 ✅ 上传 UI：详情页「上传文件夹」（webkitdirectory → rel_paths）+ zip
>   自动路由结构化端点。
> - T3 ✅ SSE 日志流：`GET /datasets/{id}/logs/stream?max_ticks=N`（默认 300
>   tick ≈ 10 分钟；测试可控）；前端 EventSource 接入。
> - T4 ✅ GitHub 源：`services/knowledge/sources/github.py`（移植自 DeepMentor）
>   + store + `PUT/GET/DELETE .../sources/github` + `POST .../sync`（后台任务）；
>   **live 验证**：octocat/Spoon-Knife README.md 同步入库并解析 DONE。
> - T6 ✅ MCP 注入能力：`ensure_knowledge_mcp_config`（.mcp.json 合并 +
>   .claude 放行）+ settings `mcp_url` 字段；本部署无激活 CLI 后端，运行时
>   验收按 D5 不做。
> - T7 ✅ zip 结构化上传→目录化→检索命中（fourier.md 0.712）；门禁全绿；
>   署名已更新。
>
> 联调实录：TE（Task Executor）进程退出导致解析卡 RUNNING——重启 TE 时发现
> 复刻环境缺 numpy/distutils（3.13 移除）链式缺失，最终以「先 import
> setuptools 再 runpy 启动模块」的 bootstrap 解决；该环境问题属 start-stack
> 部署侧，未入库存。

- 日期：2026-09-22

## 执行状态补充（2026-09-22 未完项一/二/三）

**一、部署运维**
- ✅ 标准重启：`start-stack.sh restart --skip-infra` 已执行，全栈托管（TE 心跳正常）
- ✅ `ensure_team_user` 修复 live 验证：全新用户首访自动建租户+成员（此前 102）
- ✅ 生产化：`openkg-webui start --detach` 常驻（后端 8082 / 前端 8092），
  页面与代理链路验证通过
- ✅ 身份 onboarding（D1=A）：令牌签发 + 链接 + token 模式启用；
  **建/检同源验证通过**（管理面建库 owner=local-admin，聊天面令牌委托可检索同库）

**二、功能可选项**
- ✅ T5 Web 爬取源：`sources/web.py`（同站 BFS + HTML→Markdown，SSRF 防护）
  + 端点 + WebSourcePanel；live 验证 example.com 抓取入库解析 DONE
- ✅ A5 MCP：单元验收（.mcp.json 合并/权限/禁用）+ 端点级验证
  （401 门 + initialize 200 with service key）
- ⚠️ A5 **工具调用级验收受阻**：MCP server 丢弃 `X-Intellect-*` 归因头导致
  tools/list 恒 401（详见部署文档 §5.4；issue 因 Gitee API 受限未能提交，
  已完整留档）

**三、存量迁移**
- ✅ 迁移工具 4 处 API 漂移修复（自引入后从未可运行）；dry-run 影响报告：
  4 KB / 98 chunks（详见部署文档 §5.2）
- ⚠️ **未执行 apply**：迁移会改变真实知识库（`AI技术` 91 chunks 等）的租户归属
  与可见性，属数据面变更，需你确认目标租户后再执行

- 分支：`feature/knowledge-center`（openkg-webui）；rag-app 视改动落点另立分支（预期：仅 intellect-rag 的 zip 语义不变——本阶段 rag-app 侧预期**零改动**，见 D1）
- 依据：[../knowledge-center-port-design.md](../knowledge-center-port-design.md) §六 Phase 2（A4 结构化上传 / 进度日志流 / 外部源 / A5-A6 MCP）+ 1a/1b/1.5 交付后的现状
- 流程：本清单评审通过后实施；每任务组独立提交

## 一、范围与非目标

**范围（四块）**：① zip / 文件夹结构化上传（A4）；② 解析日志流（SSE 代理推送，替代纯轮询的体验升级）；③ 外部源·GitHub 仓库同步；④ A5 MCP server 接入能力（CLI 后端）。

**非目标（另行立项）**：Web 爬取源（T5 可选拆单，技术上同 T4 模式）；per-user 令牌的批量发放运营流程；rag-app connector 体系接入（Google Drive 等既有 connector 不动）；索引版本管理（设计已裁剪）；多引擎。

## 二、关键决策点（评审重点）

| # | 决策 | 说明与建议 |
| --- | --- | --- |
| **D1** | **zip 解包位置 = openkg-webui 代理侧** | 已核实引擎侧 `file_service.py:537` 以 `FileType.OTHER` 拒收 `.zip`；且上游单请求仅支持单一 `parent_path`。代理侧解包（zip-slip 防护 + 条目数/总大小/单文件上限 + 跳过隐藏/系统文件）→ 按目录分组转发，**rag-app 零改动**。语义对齐 DeepMentor 的防御性展开（跳过隐藏/系统项） |
| **D2** | **文件夹结构 = per-file rel_paths** | web 端 `webkitdirectory` 采集 `webkitRelativePath` → 表单 `rel_paths`（与 files 对齐的 JSON 数组）→ 代理按目录分组、每组一次上游请求（`parent_path`=目录）。浏览器 File 对象无绝对路径，`webkitRelativePath` 是唯一结构来源 |
| **D3** | **日志流 = 代理侧 SSE** | 新增 `GET /api/knowledge/datasets/{id}/logs/stream`（SSE）：服务端 1s 轮询上游 ingestions、按行增量推送 `process_msg` 新增行与终态事件；前端 `EventSource` 消费（沿用 DeepMentor `useKnowledgeProgress` 的 SSE 事件形状），**保留轮询回退**（SSE 断开自动降级）。不做跨进程 WS，不动 rag-app |
| **D4** | **外部源实现位置 = openkg-webui 侧** | GitHub 同步移植 DeepMentor `services/github_source/`（Apache-2.0，约 470 行，低耦合：client/sync/sync_service）；同步 = 拉取 repo（token 可选）→ hash 变更检测 → 经既有上传链路写入 → 同步状态存 KB 偏好。rag-app connector 体系虽在，但跨仓库 + 框架成本高，且 git 拉取在 openkg-webui 侧更自然 |
| **D5** | **A5 MCP = 能力 + 文档，不做联调验收** | 本部署激活的 loop 仅 intellect-team 网关（已原生走 RAG）；MCP server（:9382 已部署运行）面向 **CLI 后端**。交付：`ensure_session_mcp_config` 模式扩展（knowledge server 条目：streamable-http + 凭据，Q6 已定 host 模式 + identity_store 用户令牌）+ `.claude/settings.json` 权限放行 + 部署文档。无激活 CLI 后端时不做运行时验收 |

## 三、任务清单

| # | 任务 | 落点 | 验收 | 估力 |
| --- | --- | --- | --- | --- |
| T1 | 结构化上传代理：`POST /datasets/{id}/documents/structured`——接收 zip（解包：zip-slip 校验 `..`/绝对路径、条目 ≤500、总大小 ≤200MB、单文件 ≤50MB、跳过 `__MACOSX`/隐藏项）与 `rel_paths` 文件夹批量；按目录分组转发上游（`parent_path`=目录）；每组结果聚合返回 | openkg-webui `api/routers/knowledge.py` | pytest：zip-slip 拒绝、条目上限、目录分组、聚合结果；真实 zip 上传后文档名含路径 | L |
| T2 | 上传 UI 升级：composer/详情页上传入口加「文件夹」（`webkitdirectory`）与 .zip 支持；rel_paths 采集；沿用进度轮询；上传摘要（N 文件 / M 目录） | openkg-webui `KnowledgeDetailPage` + `features/knowledge/api.ts` | 文件夹与 zip 上传后文档树按目录呈现 | M |
| T3 | SSE 日志流：代理 SSE 端点（登录门 + 增量行推送 + `done`/`error` 终态事件 + 客户端断开清理）；前端 `EventSource` 接入详情页（替换轮询日志区，保留轮询回退） | openkg-webui 代理 + `KnowledgeDetailPage` | 解析期间日志行实时滚动；SSE 断开自动回退轮询 | M |
| T4 | 外部源·GitHub 同步：移植 DeepMentor `services/github_source/`（client/sync/sync_service，标注 Derived from DeepMentor）；来源配置（repo/branch/glob/token 可选）与同步状态（commit hash、上次同步、文件清单）存 KB 偏好；同步任务（asyncio 任务 + 状态轮询）；代理端点（PUT/GET/DELETE source、POST sync）；详情页「外部源」tab | openkg-webui `services/knowledge/sources/` + 代理 + UI | 配置 repo → 手动同步 → 新增/变更文件入库（文档列表可见）；二次同步仅增量；删除源不删已入库文档（文档说明） | **L** |
| T5 | 外部源·Web 爬取（可选拆单）：移植 DeepMentor `services/web_source/`（crawler/markdown），入口同 T4 模式（URL/限深/限页） | openkg-webui | 指定 URL 站点抓取转 markdown 入库 | L（独立评审后实施） |
| T6 | A5 MCP 接入能力：`services/kag/__init__.py` 的 `ensure_session_mcp_config` 模式扩展——knowledge 条目（intellect MCP streamable-http + host 模式用户令牌，Q6 决议）+ `.claude/settings.json` 放行 `mcp__intellect__intellect_retrieval`；仅 CLI 家族激活时注入；部署文档补充 | openkg-webui `services/kag/`（或抽公共模块）+ docs | CLI 会话 workdir 生成 `.mcp.json`（含令牌/权限放行）；无激活 CLI 后端时仅交付文档 | S-M |
| T7 | 验收 + 门禁：① zip/文件夹结构化上传 → 目录化文档树 → 解析 → 检索；② SSE 日志实时滚动 + 断线回退；③ GitHub 源同步闭环；④ CLI 后端 MCP 冒烟（如可行）；⑤ 全部门禁；⑥ DeepMentor 署名随 T4 落实 | 全链 | §五清单 | S |

## 四、实施顺序与提交切分

```
T1 → T2（结构化上传，两提交）
  → T3（SSE 日志流，一个提交）
  → T4（GitHub 源，两个提交：服务移植 / 端点+UI）
  → T6（MCP 能力 + 文档）→ T5（可选，另评审）→ T7（验收）
```
T5（Web 爬取）默认**不在本阶段实施**，评审时确认是否并入。

## 五、Phase 2 验收清单

1. zip 上传：嵌套目录 zip → 文档按目录呈现 → 解析 → 检索命中 zip 内内容；zip-slip 样本（`../evil.txt`）被拒且无副作用。
2. 文件夹上传：浏览器选择文件夹 → 目录结构保留 → 全量解析完成。
3. 解析期间日志实时滚动；SSE 断开自动回退轮询，无报错。
4. GitHub 源：配置 repo → 同步 → 文档入库且内容可检索；重复同步增量；无 token 的公共 repo 可用。
5. （如交付 T6）CLI 会话 workdir 生成含 knowledge server 的 `.mcp.json` 与权限放行；文档说明凭据形态与局限。
6. 门禁全绿；DeepMentor 署名更新（THIRD_PARTY_NOTICES 追加 external source 服务）。

## 六、风险

- **zip-slip / 炸弹**：解包在校验后逐条目进行（路径规范化 + 黑名单前缀 + 总量熔断）；测试覆盖恶意样本。
- **分组请求数**：深目录大文件夹 → 上游请求数 = 目录数；设目录数上限（如 200）并在 UI 预警。
- **GitHub API 限流**：匿名 60 req/h——支持可选 PAT 配置（KB 偏好内，write-only 掩码）。
- **SSE 连接占用**：代理轮询上游占用工作线程；并发上限（每会话 1 条流）+ 心跳与空闲超时。
- **MCP 凭据落盘**：`.mcp.json` 写入会话工作区（服务器侧）；Q6 已定 host 模式用户令牌——文档明确令牌权限边界。
