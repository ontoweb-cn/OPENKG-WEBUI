# 知识中心 Phase 1b 实施任务清单（评审稿）

> **执行状态（2026-09-22，评审后实施完成）**
>
> 评审修订（实施前确认）：**D2/D3 改写**——发现网关 runs 请求体原生支持会话级
> `rag` 块（`params.rag.enabled/scope/knowledge_base_ids` → `build_session_config`，
> agent_api.rs 测试锁定），因此召回范围不落 config.yaml、也不改 intellect-team：
> **openkg-webui 在 runs payload 携带 `rag` 块**（settings 驱动，`chat_scope` 字段
> 默认 tenant）。D3（/retrieval 401 调用者）确认非本链路：网关因 F4 短路不发检索，
> 401 来自 intellect-webui 的会话检索（缺成员身份头），记为独立事项。
>
> 实施结果：
> - T1 ✅ runs payload 注入 `rag` 块（knowledge 启用时；未启用请求体逐字节不变）；
>   settings 增 `chat_scope`（tenant/team/project/auto，非法回落 tenant）+ 设置页选择器。
> - T3 ✅ 回归测试 3 例（rag 块注入两态 + `tool.completed+error:true`/`tool.failed`
>   均映射 is_error），runs 后端 30 过。
> - T2 ✅/⚠️ 网关级验收通过：runs 携带 rag 块 → 执行检索（/retrieval 全部 200，
>   多轮查询）→ Agent 诚实作答；`knowledge_base_ids` 定向在 rag-app API 层验证
>   正确（scope=tenant 与 dataset_ids 均只见本租户库，**隔离无泄漏**）。
>   ⚠️ **遗留（2026-09-22 二次分析修正结论）**：原判定"网关检索泄漏其他租户
>   文档"**不成立**。证据矩阵：① scoped run 引用的文档（AI技术=private/
>   owner 2d0b100f273a；场景图谱综述=tenant 级/属 0000 租户）对身份
>   **2d0b100f273a**（网关自己的服务成员：前者 owner、后者为其已加入租户的
>   tenant 级库）完全合法可见；② 以 2d0b100f273a + scope=tenant 复现检索，
>   结果与 agent 所见一致（total 7）；③ 以 2d0b100f273a + dataset_ids=[测试库]
>   检索 → `denied_dataset_ids` 精确拒绝（访问控制无泄漏）。
>   **真实缺陷是身份归因断链**：网关检索以自身服务成员（2d0b100f273a）执行，
>   而非 run 的归因成员（openkg-webui 用户 local-admin）——后者 scope=tenant
>   只见 1 个分块，前者可见 7 个。后果：(a) openkg-webui 用户会话可能召回
>   服务成员可见但其自身不可见的内容（越权暴露方向）；(b) per-run
>   knowledge_base_ids 对服务成员不可见的库静默失效（denied）。
>   **修复方向（intellect-team 仓库）**：run 路径构建 RAG provider 时以
>   member_context（即 openkg-webui 归因的成员）构建 identity，替代静态服务
>   身份；验收=以 local-admin 会话检索时 rag-app 侧收到的 X-Intellect-User 为
>   mem_local-admin 且结果集=其可见集。1.5（B2 精确库引用）以此为前置。
> - T4 ✅ [../knowledge-center-deployment.md](../knowledge-center-deployment.md)。
>
> 后续：Phase 1.5（composer 勾选 + knowledge_base_ids 透传）以网关身份修复为前置。

- 日期：2026-09-22
- 分支：`feature/knowledge-center`（openkg-webui）／ intellect-team 与 intellect-rag-app 按改动落点另立分支
- 依据：[../knowledge-center-port-design.md](../knowledge-center-port-design.md) §五/§六/§九/§十一；Phase 1a 已验收
- 流程：本清单评审通过后实施；Phase 1.5 / 2 各自再走"细化→评审→实施"

## 一、范围与非目标

**用户可见结果（本阶段定义）**：聊天 turn 中 Intellect RAG 召回真正工作——网关会话按租户范围召回并注入上下文，`search_knowledge` 工具可用，工具事件在 openkg-webui 界面正确显示；P1-1 用例①（turn 侧）闭环。

**非目标**：composer 勾选与 B2 run 级 kb_ids 透传（**1.5**）；A3 默认知识库（**建议移至 1.5**，见 D1）；A5/A6 MCP server（本部署激活的 loop 只有 intellect-team 网关，按 §九-9.3 推论整体延后）；CLI grounding 块（无激活 CLI 后端）；Python api_server 侧（本部署 :9091 由 **Rust 网关**服务，§十一-6 已确认）。

## 二、侦察发现（2026-09-22，已完成 B0 大半）

| # | 事实 | 依据 |
| --- | --- | --- |
| F1 | :9091 由 **Rust 网关**（intellect-gateway）监听，openkg-webui 的 runs 实际由它服务 | `ss` 端口归属 + agent-loop profile url |
| F2 | 网关环境已启用 RAG：`RAG_SERVICE_URL=http://127.0.0.1:9380` + `INTELLECT_RAG_API_KEY`（与 docker/.env **同一把**，§十一-2 成立） | `/proc/<pid>/environ` |
| F3 | `~/.intellect/config.yaml` 的 `rag:` 块已配置（enabled / provider: intellect-rag / prefetch hybrid + 关键词），**但没有 `scope` 也没有 `kb_ids`** | config.yaml:321 |
| F4 | Rust provider `do_search` 要求显式 `kb_ids` 或 `scope`，否则**短路返回空**（"RAG disabled: no explicit kb_ids and no scope configured"）——即当前 openkg-webui 的 turn 很可能**根本没有发生检索**，而非检索失败 | `intellect-core/src/rag_http.rs:218-232` |
| F5 | rag-app 日志显示 `POST /api/v1/retrieval → 401`（多次，联调时段）；复现确认：**无 `X-Intellect-User` 头 → 401，有 → 200**（key 正确）。401 的调用者另有其人（疑为 intellect-webui 的会话级检索）——网关侧因 F4 短路，不会发出 401 | api.log + curl 复现 |
| F6 | Rust provider 具备身份头机制（`identity.apply(req)`），401 与否取决于该身份在 run 路径上的构建来源 | `rag_http.rs:230` |
| F7 | openkg-webui 对 runs 工具事件的映射**已正确处理** intellect-team 的 `tool.completed + error:true` 约定（`is_error: kind == "tool.failed" or bool(obj.get("error"))`，http_backend.py:935）——§九-9.2 集成注记项无需改动，仅需回归测试锁定 | http_backend.py:918-935 |

## 三、关键决策点（评审重点）

| # | 决策 | 建议 |
| --- | --- | --- |
| D1 | **A3 默认知识库移至 1.5** | 原方案 A3 在 1b；但无 B2（run 级 kb_ids）时 per-user 默认库没有任何消费者（网关召回范围是租户/会话级）。移到 1.5 与 B2 一起做才有意义。**建议：移出。** |
| D2 | **Phase 1b 的召回范围** | 建议 `rag.scope: tenant`（config.yaml rag 块加一行）：租户内全部库参与召回。副作用：该默认对网关**所有**会话生效（含 intellect-webui 的会话——它们本就有会话级 scope，显式配置优先，不受影响）。备选：显式 kb_ids 白名单（每次建库要改配置，不可取）。 |
| D3 | **检索 401 的修复落点** | 待 T0 定位调用者后定：若是 intellect-webui 会话检索缺身份头 → 修其会话配置或网关身份透传（intellect-team 仓库变更，走该仓库评审）；若 T0 显示网关路径也会发出无身份调用 → `rag_http.rs` 的 identity 构建修复（同为 intellect-team 仓库）。openkg-webui 仓库预计**无改动**。 |

## 四、任务清单

| # | 任务 | 落点 | 验收 | 估力 |
| --- | --- | --- | --- | --- |
| T0 | 诊断收尾：定位 /retrieval 401 的实际调用者与身份构建路径（rag-app 访问日志时间关联 + 网关 run 路径的 identity 构建代码 `identity.apply` 来源 + intellect-webui 会话配置）；确认 config.yaml 加 scope 后 openkg-webui turn 的召回确实触发（TE/embedding 正常，Phase 1a 已证） | intellect-team 仓库（只读诊断） | 产出：401 根因结论 + D3 落点选型（写回本文档） | S-M |
| T1 | 配置召回范围：config.yaml `rag:` 块加 `scope: tenant`（决策 D2）；如 T0 发现会话级覆盖问题则按 D3 处理 | intellect-team 部署配置 | 网关会话 turn 触发 /retrieval → 200；召回内容进入 turn 上下文（网关日志/prefetch debug 证据） | S |
| T2 | P1-1 用例①（turn 侧）：聊天中提问命中 UI 建库内容——用 openkg-webui 发起 turn（"根据知识库，傅里叶变换是什么？"），确认召回块含联调测试库内容且引用/工具事件正常显示 | openkg-webui + intellect-team 联调 | turn 输出引用知识库内容；活动面板工具事件无异常渲染 | M |
| T3 | 回归测试锁定 F7：为 runs 工具事件映射补单测（`tool.completed + error:true → is_error`；`tool.failed → is_error`），防回归 | openkg-webui tests | pytest 过 | S |
| T4 | B3 部署文档：`docs/knowledge-center-deployment.md`——RAG_SERVICE_URL / INTELLECT_RAG_API_KEY（同一把）/ config.yaml rag 块（scope 语义，§十一-5 可见范围）/ 常见坑（本 Phase 实录：retrieval 401 缺身份头、租户未建 → 102） | openkg-webui docs | 文档覆盖 Phase 1b 全部配置项 | S |

## 五、实施顺序与提交切分

```
T0（诊断结论回填 §三 D3 → 评审确认修复选型）
  → T1（intellect-team 配置/代码变更，独立提交；如涉 Rust 代码则先跑其测试）
  → T2（联调验收，截图/日志留档）
  → T3 + T4（openkg-webui 提交）
```

## 六、Phase 1b 验收清单

1. openkg-webui 发起聊天 turn，问题命中知识库内容，回答引用库内信息（P1-1 用例①的 turn 侧）。
2. rag-app 日志可见对应 /retrieval 200（不再有 401）。
3. 工具事件（search_knowledge / 检索）在 openkg-webui 活动面板正常渲染，失败路径显示为错误（F7 回归测试过）。
4. 其他用户（如 testreview01）的会话检索**看不到** local-admin 的库（P1-1 用例②的 turn 侧，沿用 1a 的隔离数据）。
5. 门禁：两仓库相关测试全绿。

## 七、风险与遗留

- **网关行为变更面**：`rag.scope: tenant` 是部署级默认，影响网关全部会话的召回行为——intellect-webui 会话若有显式 scope 不受影响，但无显式配置的会话将从"无检索"变为"租户级检索"。上线前与 intellect-webui 使用方确认。
- **Rust 改动风险**（若 D3 落到 rag_http.rs）：改动在 intellect-core，需跑 intellect-team 的 Rust 测试套件。
- **1.5 预告**：B2（openkg-webui runs body + 网关会话配置透传 kb_ids/scope）+ composer 勾选 + A3 默认库；注意 B2 落点是 **Rust 网关**而非 Python adapter（§十一-6/F1 修正了 §九-9.2 的落点描述）。
