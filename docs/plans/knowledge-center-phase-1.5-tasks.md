# 知识中心 Phase 1.5 实施任务清单（评审稿）

> **执行状态（2026-09-22，评审后实施完成）**
>
> 前置复核（D1=A 生效验证）：网关 #27 修复部署后（gateway 11:14 重启），以
> **local-admin 的 member token 委托**发起 scoped run
> （`rag.knowledge_base_ids=[测试库]`）→ **回答精准引用测试库原文**
> （`CITES-KB: True`）——1b 被阻塞的场景正向闭环，D1=A 链路（token 委托 →
> 网关 → 检索 → 引用）全通。
>
> 实施结果：
> - T1 ✅ 契约链路四段：`TurnRequest.knowledge_bases`（既有字段，零协议变更）
>   → `UnifiedContext.knowledge_bases`（executor 映射 + A3 回落）→
>   `AgentLoopRequest.knowledge_kb_ids` → runs payload。
> - T2 ✅ 优先级矩阵：勾选 kb_ids > 部署默认 chat_scope > 不发；kb_ids 非空
>   省略 scope；知识中心关闭 = off（回归测试 4 例）。
> - T3 ✅ composer 上方「附加知识库」选择条（列表/勾选/chip），勾选集持久化
>   到会话偏好（`PUT /api/sessions/{id}/knowledge-selection`），跨刷新保持。
> - T4 ✅（D4 修订）A3 存 openkg-webui 侧 per-user 偏好文件
>   （`<user_data_dir>/knowledge_defaults.json`）+ `GET/PUT
>   /api/knowledge/preferences`，rag-app 零改动。
> - T5 ✅ 身份引导依赖 1a 的 identity_ok 门控与 agent-loop 设置页 identity 卡片。
> - T6 验收：**线级闭环达成**（上表 token 委托探针）；UI 层 picker 渲染/勾选
>   chip 截图验证 ✓；无头驱动完整聊天交互受 dev 水合时序影响未稳定跑通
>   （非产品缺陷，生产构建不受影响）。
>
> 门禁：后端 pytest 473 过（首轮 2 例偶发，复跑全绿）；web typecheck/lint/
> i18n/contracts/architecture 全过。已提交推送。

- 日期：2026-09-22
- 依据：[../knowledge-center-port-design.md](../knowledge-center-port-design.md) §五/§九/§十一 + Phase 1b 联调新事实；前置（网关身份归因修复，ontoweb-cn/intellect-team#27）**已部分达成**——过度暴露已修，但暴露出身份平面错位（见 D1）
- 流程：本清单评审通过后实施

## 一、前置验证结果（2026-09-22 实测，修正 1b 遗留的解读）

| 项 | 状态 |
| --- | --- |
| 网关过度暴露（服务成员 7 分块 vs 用户 1 分块） | ✅ 已修：网关现以 runs 凭据所属成员执行检索（#27 验收标准达成） |
| openkg-webui → 网关的凭据 | **`imt_` 成员令牌**（member_api_tokens 名 "DeepMentor"，成员 `2d0b100f273a`）——44 字符即 imt_ + 40 hex |
| 安全语义 | imt_ 鉴权下 `X-Intellect-User` 头**故意不采信**（防伪造，#27 修复的原则），检索身份 = 令牌所属成员 |
| `rag.knowledge_base_ids` 定向测试库 | ⚠️ `denied_dataset_ids` 精确拒绝——该库由管理面 per-user 头归因建户（owner=local-admin）创建，而聊天面身份是 2d0b100f273a |

**结论**：检索通道本身健康（200/拒绝行为均精确）；阻塞 1.5 核心特性的是 **openkg-webui 自身的身份平面错位**——管理面（建库）按 per-user 归因写，聊天面（检索）按固定令牌成员读。

## 二、关键决策点（评审重点，D1 定调后才可实施 T1-T5）

| # | 决策 | 建议 |
| --- | --- | --- |
| **D1** | **知识平面的身份模型** | **A：per-user token 委托（推荐）**——每个用户在 `/settings/agent-loop` 完成 Intellect 账号链接（identity_store 按用户存 member token，**机制已实现**）；管理面与聊天面都以该用户令牌执行 → 建/检同源，隔离纯净（P1-1 原始架构）。**B：单服务成员**——管理面也改用 DeepMentor 令牌写，所有库归服务成员，kb_ids 过滤由 openkg-webui 自己兜 → rag-app 层隔离丧失，仅适合单团队内部部署。若选 A，未链接用户：知识中心可用（管理面 header 归因），但聊天召回需链接后生效（界面引导，复用 1a 的 identity_ok 门控） |
| D2 | **召回语义矩阵**（勾选/未勾选/无 A3） | 显式勾选（kb_ids，勾选库须该用户可见，否则 denied 提示）> A3 默认库（若配置）> `settings.chat_scope`（1b 已有，tenant 默认）> 全无 → 不发 rag 块（现状）。优先级自上而下，显式配置永远覆盖部署默认 |
| D3 | **契约扩展形态** | `AgentLoopRequest` 增加 `knowledge: {kb_ids?: [dataset_id], scope?: str}` 字段（dataset_id 由 composer 直接存，无需名称解析）；同步 WS 模型与 contracts 三件套。仅 runs 协议消费；HTTP turn 家族忽略该字段 |
| D4 | **A3 落点** | **openkg-webui 侧 per-user 偏好文件**（`data/users/<uid>/` 下 JSON，存默认库 dataset_id 列表）——评审 R6 简化：A3 只是"默认勾选集"，dataset_id 跨身份模式稳定，rag-app 零改动、无新端点/新表 |

## 评审记录（2026-09-22，D1=A 后逐项核验）

**R1（P1）D1=A 的"建/检同源"以统一身份为前提，存量库存在归属错位。**
已核实机制：链接后 turn 以用户 member token 执行（`with_identity` 整体替换 api_key，http_backend.py），rag-app 侧用户 = 令牌成员；而**存量库**是 header 归因期以 `local-admin` 身份创建的（owner=local-admin），token 模式下该成员不可见 → 链接后老库"消失"（1b 联调的 denied 即此错位的实例）。处置：
- T0 增加核实项：`simon` 分支新增的「KB 租户归一迁移工具」（a6ff2d4）能否承担存量库 owner/租户改绑；
- Phase 1.5 范围限定："**链接后新建库**才保证可见"；存量库迁移单列（依赖该工具评估）。
另注意双模式成员语义差：header 归因 = 本地账号派生 id（local-admin），token = 链接的 intellect 成员（如 2d0b100f273a）——同一人在两平面"用户"不同。A3/T4 按 openkg-webui 侧存储可规避此漂移。

**R2（P1）契约链路比 T1 描述的长。** 实际为四段：`TurnRequest`（WS 模型）→ `UnifiedContext`（新增字段）→ `AgentLoopRequest.knowledge` → runs payload；contracts 三件套 + 1b 的全局设置组装点同步改为"请求优先"。T1 估力 M→M+，且 **T1/T2 必须同批落地**（否则字段无人消费）。

**R3（P2）链接 onboarding 为现成能力。** `POST /api/settings/agent-loop/identity` 接受凭据换令牌或直贴令牌，预验证 + origin 绑定 + per-user 存储（`identity_store_for(user_id)`）；agent-loop 设置页已有 identity 卡片。T5 从"建引导"降为"页面内引用既有卡片"。

**R4（P2）T6-② 验收不可实现为 UI 结构化提示，修正。** `denied_dataset_ids` 只存在于检索 JSON 文本内（网关向 agent 返回空结果 + degraded 说明），UI 无法结构化感知"无权限"。修正验收为：**agent 回答如实说明"勾选的库无权访问/无内容"**；结构化提示需网关事件增强，超范围。

**R5/R6（P3，确认项）**：`build_retrieval_body` kb_ids 优先于 scope ✓（T2"kb_ids 非空省略 scope"与网关语义一致）；`with_identity` 整体替换已实现 ✓（token 模式无需后端改动）；会话偏好 API 已有（`update_session_preferences`）✓。

**评审后的任务修订**：T0 增加「a6ff2d4 迁移工具能力核实」；T1 按 R2 扩为四段链路、与 T2 同批；T4 按 D4 修订缩为 openkg-webui 单仓库（per-user 偏好文件，不新增 rag-app 端点/表）；T5/T6 按 R3/R4 修正。**D1=A 的建/检同源前提（R1）是范围与迁移策略的开关，请评审确认。**

## 三、任务清单

| # | 任务 | 落点 | 验收 | 估力 |
| --- | --- | --- | --- | --- |
| T0 | 前置双核实：① #27 修复部署形态下 `AgentConfig.kb_ids` → provider `do_search` dataset_ids 传导（rag_http.rs 现版）；② a6ff2d4「KB 租户归一迁移工具」能否承担存量库 owner/租户改绑（R1 迁移路径） | intellect-team（只读）+ intellect-rag-app（只读） | 探针 run 引用 kc-acceptance.txt（1b 被 denied 的同一请求）；迁移路径结论写回本文档 | S |
| T1 | 契约扩展**四段链路**（R2）：`TurnRequest`（WS 模型）→ `UnifiedContext.knowledge` → `AgentLoopRequest.knowledge {kb_ids, scope}` → contracts 三件套；HTTP/CLI 家族忽略；与 T2 同批落地 | openkg-webui | schema 漂移检查过；operationId 无冲突；可选字段老会话零影响 | M+ |
| T2 | runs payload 组装优先级矩阵（D2）：`RunsAgentLoopBackend.run()` 中 rag 块按 勾选 > A3 > chat_scope > 不发 组装；kb_ids 非空时省略 scope（网关 kb_ids 优先语义）；与 T1 同批 | openkg-webui | pytest：矩阵四分支 | M |
| T3 | composer 勾选 UI + 会话持久化：聊天输入区新增知识库多选（数据源复用 `features/knowledge/api.fetchDatasets`）；勾选集存 `session.preferences_json`；turn 请求带上 | openkg-webui | 勾选 → 发消息 → 网关收到的 kb_ids 一致（日志）；刷新页面勾选态保持 | M-L |
| T4 | A3 默认知识库（D4 修订）：openkg-webui per-user 偏好文件（`data/users/<uid>/knowledge_preferences.json`，services/knowledge 内加存取）；设置页/详情页加「设为默认知识库」；A3 库进 T2 优先级链 | openkg-webui（单仓库） | PUT 后 turn 默认召回该库；未勾选时生效；rag-app 零改动 | S-M |
| T5 | 身份引导（D1=A 配套）：identity_ok=false 时聊天侧提示「链接 Intellect 账号以启用知识召回」，链至 `/settings/agent-loop` | openkg-webui | 未链接用户有明确引导，无静默失败 | S |
| T6 | 验收 + 门禁：① 勾选库引用原文（1b 被阻塞的同一请求）；② 未勾选走 A3/chat_scope；③ 双用户隔离（P1-1 ②turn 侧）；④ 勾选无权限库时 agent 回答如实说明"无权访问/无内容"（R4：UI 结构化提示不可行）；⑤ 全部门禁 | 全链 | §六清单 | S |

## 四、实施顺序与提交切分

```
T0（确认 #27 修复部署形态与 kb_ids 传导）
  → D1/D2/D3/D4 评审确认
  → T1 → T2（后端两提交）
  → T4（rag-app 一个提交 + openkg-webui 一个提交）
  → T3 → T5 → T6（前端两提交）
```

## 五、风险

- **turn 契约扩展**（T1）是 1.x 系列唯一触及核心协议的改动——严格按 merge-feasibility §5.2 三件套执行；字段可选，老会话零影响。
- **身份链接 onboarding**（D1=A）：多用户部署要求每人链接 Intellect 账号，是产品成本；未链接用户的降级体验必须明确（T5）。
- **网关 kb_ids 字段语义**：`rag.knowledge_base_ids` 期望 rag-app dataset UUID（1b 实测 denied 行为即按此解析）；composer 存 dataset_id 正好对齐，但需在 T0 复核 #27 修复后无字段名变化。
- **A3 表设计**（T4）：per-user settings 表需迁移脚本；对齐 rag-app 既有 migration 风格（参照 `api/db/migrations/009_ensure_default_tenant_owner.py`）。

## 六、Phase 1.5 验收清单

1. 勾选「联调测试库(1)」→ 聊天提问傅里叶变换 → 回答引用 `kc-acceptance.txt` 原文（1b 被阻塞场景的正向闭环）。
2. 勾选另一用户私有库 → agent 回答如实说明"该库无权访问/无内容"（R4：denied 仅存在于检索文本，UI 结构化提示不可行）。
3. 未勾选 + 有 A3 默认库 → 默认库召回；全无 → 无召回且无报错（语义矩阵 D2 全分支）。
4. 双用户 turn 侧隔离：A 的会话检索不到 B 的私有库（P1-1 ②完成）。
5. 门禁全绿（后端 pytest / web typecheck+lint+contracts+i18n / rag-app 测试）。
