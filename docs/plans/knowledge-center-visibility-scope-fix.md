# 知识中心可见范围修复方案：租户级知识库不可见 + 团队级永不生效

- 日期：2026-09-25
- 状态：**P0-A/P0-B/P0-C/P1 已实施并实机验收（2026-09-25）**；
  **P2（团队/项目维度）裁决为本期不实现，列入 TODO**（见
  [knowledge-center-port-design.md](knowledge-center-port-design.md) §十三）
- 实施结果：上游 194 项 + 本仓库后端 1740 项（23 项预存失败，见 §六）+ 前端
  61 vitest + 690 node 全过；实机 `local-admin` 列表 1 → 3 条，检索 8 → 12
  chunks，非成员隔离验证通过
- 现象来源：用户反馈"知识中心只显示 private 知识库，租户/团队/项目级知识库不显示"
- 影响仓库：`intellect-rag`（P0-A）、`openkg-webui`（P0-B/P1）、`intellect-rag-app`（测试）、
  `intellect-team`（P2）
- 涉及上游文件：`intellect-rag/api/db/access_control.py`、
  `intellect-rag-app/api/apps/restful_apis/dataset_api.py`、
  `intellect-rag-app/api/apps/services/dataset_api_service.py`、
  `intellect-rag-app/api/apps/restful_apis/chunk_api.py`

## 一、现象与证据（实机复现）

本机部署（`identity_mode: token`，个人租户模型，`local-admin`），
`knowledgebase` 表 13 行，其中与身份相关的 5 行：

| name | tenant_id | permission | visibility | owner_user_id |
| --- | --- | --- | --- | --- |
| 联调测试库(1) | `local-admin` | me | private | `local-admin` |
| AI技术 | `2d0b100f273a` | me | private | `2d0b100f273a` |
| 场景图谱研究综述 | `0000…0000` | team | **tenant** | `2d0b100f273a` |
| 3D场景图预测论文 | `0000…0000` | team | **tenant** | `2d0b100f273a` |
| （7 行 session-* ） | `0000…0000` | me | private | `mem_2d0b100f273a` |

`local-admin` 的 `tenant_membership` 存在 `0000…0000`（normal）与 `local-admin`（normal）两行，
即**该用户确实是企业默认租户的成员**，那 2 条 `visibility=tenant` 的库本应对其可见。

实测上游 `GET /api/v1/datasets`（KC 实际使用的凭据）：

```
member token（KC 现状）            → total=1，仅「联调测试库(1)」(private)
member token + X-Intellect-Tenant: 0000…0000 → total=2，仅 2 条 tenant 级库
                                     （私有库消失——两者不可并存）
```

检索路径同样漏检（`POST /api/v1/retrieval`，`scope=tenant` 与 `scope=auto`）：

```
code=0，total_chunks=8，dataset_aggs={4a582b6a…: 8}   ← 只有私有库
```

**结论：租户级库在管理面列表与检索面同时缺席，且两条路径机制相同。**

## 二、根因

### A. 单租户上下文 → 租户级行被过滤层误杀（本次现象的直接原因）

`intellect-rag-api` 的 `filter_kb_rows_by_scope`（`api/db/access_control.py:524-612`）
从身份上下文推导可见租户集合：

```python
tenant_ids = {g.id for g in subject_context.groups_of_type("tenant")}  # 只有当前上下文的一个租户
if tenant_id:
    tenant_ids.add(tenant_id)
...
tenant_visible = (visibility == "tenant" or legacy_team_as_tenant) and row_tenant in tenant_ids
```

而其 docstring 描述的是 "tenant-visible rows of the subject's **tenants**"（复数）。

**取数层比过滤层宽**，这是数据被丢掉的机制：

| 路径 | 取数层 | 过滤层 | 结果 |
| --- | --- | --- | --- |
| 列表 `/datasets` | `get_joined_tenants_by_user_id`（**全部**加入租户） | 单租户上下文 | 多租户行取回后被剔除 |
| 检索 `/retrieval` scope 分支 | `get_joined_tenant_ids`（**全部**加入租户，`knowledgebase_service.py:261-262`） | 同一过滤函数，仍单租户 | 同上 |
| 检索显式 `dataset_ids` 分支 | 按 id 取 | 同一过滤函数 | 同上（表现为 `denied_dataset_ids`） |

`filter_kb_rows_by_scope` 共 3 个调用点（`dataset_api.py:496`、`dataset_api_service.py:1466`、
`chunk_api.py:337`），**外加** `get_accessible_ids_by_scope` 内部一处（`knowledgebase_service.py:292`），
全部命中同一缺陷。修此一处即覆盖四个入口。

> **评审更正（本次实施中修正）**：初版评审曾判断"检索的 scope 路径已走并集、不受影响"——
> 该判断错误。`get_accessible_ids_by_scope` 的 `joined` 集合**只用于取数**，并未传给过滤函数；
> 过滤函数只认 `subject_context.groups`。实机检索基线（§一）已证伪该判断。

### B. visibility 由 header 决定，而 KC 从不发 Team/Project 头

上游 `api/utils/ownership.py:51-56`：

```python
def _compute_visibility(team_id, project_id):
    if team_id:
        return "team"
    if project_id:
        return "project"
    return "private"
```

取值来自 `dataset_api.py:145-150` 的 `subject_context.team_id/project_id`，即
`X-Intellect-Team`/`X-Intellect-Project` 头。

KC 侧链路断裂在三处：

1. `identity.py:544-546` 确实尝试写这两个头；
2. 但 `_set_header`（`identity.py:212-223`）对空值直接跳过；
3. 而 `LinkedIdentity.team_id/project_id` **永远为空**——`_member_identity`（`identity.py:323-329`）
   构造时未填，网关 `handle_me` 的 `MeResp`（`intellect-team/intellect-gateway/src/platform/members_api.rs:577-583`）
   只返回 `id/display_name/role/enabled/email`，**不含 team/project**。

因此 KC 建的库 `visibility` 恒为 `private`，**即使 UI 上选了"共享给团队"**。更糟的是：

- 上游 `create` 用 `subject_context.team_id/project_id` 写 ownership，**无 clamp、无查表**；
- 而 `sync_membership.py:452-457` 对 `imt_` 令牌**拒绝**据头建成员关系。

两者叠加的产物是**一个 team_id 可伪造、且无人可见的孤儿库**（实测发
`X-Intellect-Team: engineering` 返回 0 条即此语义）。所以 P1 的"禁用该选项"
不只是文案问题，而是**阻断一条会产出坏数据的操作路径**。

### C. 前端徽标读 `permission`，而可见性看 `visibility`

`KnowledgeDataset.permission`（`web/features/knowledge/model.ts:37`、`KnowledgeHomePage.tsx:149-151`）
展示 "Team"/"Private" 徽标，但**上游访问控制不看 `permission`**（legacy 字段，
且 `update`/`create` 路径已 `req.pop("permission")`，`dataset_api_service.py:272`）。
徽标与真实可见性可以背离。

### D. 主体身份取自租户回退（P0-B 的动机）

`dataset_api.py:445`：

```python
accessible_user_id = intellect_user_id if intellect_user_id is not None else tenant_id
```

token 模式不发 `X-Intellect-User`，于是**主体身份退化为 `X-Intellect-Tenant` 的解析值**
（`resolve_tenant_id`，未做主体钳制）。后果：

- 本实例（personal-tenant，tenant == user）恰好无害；
- 但部署一旦设置 `INTELLECT_TENANT_ID`，主体就不是成员 id，**private 库会整体消失**
  （owner 短路失败），且并集修复会以该值为 basis 扩大可见集。

## 三、方案

### P0-A 上游：租户集合改为"已加入租户 ∪ 上下文租户"（1 文件约 4 行）

`intellect-rag/api/db/access_control.py` 的 `filter_kb_rows_by_scope`：把
`tenant_ids` 的构造对齐到同仓库既有口径 `get_accessible_ids_by_scope`
（`knowledgebase_service.py:249-263`），复用同文件 `get_joined_tenant_ids`
（**同模块调用，无新依赖**）：

```python
tenant_ids = set(get_joined_tenant_ids(acting_user)) if acting_user else set()
if subject_context is not None:
    tenant_ids |= {g.id for g in subject_context.groups_of_type("tenant")}
if tenant_id:
    tenant_ids.add(tenant_id)
```

保留的既有语义（不得放宽）：

- `can_access_resource` 仍是逐行门（fail-closed，异常→不返回 id）；
- `visibility == "private"` 仍仅 owner 可见；
- `get_joined_tenant_ids` 自带"用户永远是自租户成员"的 legacy 语义（`access_control.py:173-174`）。

**唯一行为扩张**：`visibility=tenant` 的库会出现在其**所属租户成员**的列表里
（此前"可按 id 访问但不列出"）。落地前需确认"默认租户=全员共享"是部署意图。

### P0-B KC：token 模式补发 `X-Intellect-User`（主体钉定）

在 `services/knowledge/__init__.py` 的 `resolve_request_auth` 中，token/token_required
模式且 `identity.member_id` 非空时补写 `X-Intellect-User`。

安全性依据（上游代码级）：

- `parse_intellect_headers` 剥离可选 `mem_` 前缀（`api/identity/context.py:78-85`）；
- `resolve_subject_id` → `bind_subject_id`（`api_utils.py:261-284`）：头与已认证主体
  不一致且认证类型非 `RAG_SERVICE`/可信 BFF 时**钳制为已认证主体**；
- `sync_membership`（`sync_membership.py:452-457`）token 路径**忽略**该头做身份，
  故不会改变成员关系写入。

仅改 KC 代理层，**不改** `agent_loop/identity.py`（其 token 模式丢弃该头是 turn 路径的
有意设计，不在本次范围）。

### P0-C 验收

1. 不变量：对任意用户 U，列表结果 ≡ 逐库 `can_access_resource(KB, actor=U)`（可证伪，
   不绑定本实例数据）；
2. 本实例 `local-admin` 列表应为 3 条（1 私有 + 2 租户级）；
   **检索 `scope=tenant`/`auto` 对每条库按其自身内容均可召回**
   （不要求单次查询同时命中三库——检索是查询相关的，原"`dataset_aggs` 含 3 个
   id"的写法不可证伪，已按此意更正）；
3. 非成员用户不可见这 3 条；
4. 上游 `test/unit_test/test_access_control.py` 全绿并新增多租户用例；
   分页总数（`total_datasets`）与过滤后集合一致。

### P1 KC 前端：说实话 + 阻断坏数据路径

1. **阻断优先**：`visibility` 不可能是 `team`/`project` 时（当前恒真），禁用/隐藏
   "共享给团队"选项并给出说明——先于徽标修复，因为它阻止坏数据产生；
2. 域模型补 `visibility` 字段（`model.ts`、`engines/models.py` 出站），徽标改读
   `visibility`（private→"私有"、tenant→"组织"、team→"团队"、project→"项目"）；
3. i18n 文案补齐（react-i18next `useTranslation`）。

### P2 三仓库：打通团队/项目维度 —— **本期不实现，列入 TODO（2026-09-25 裁决）**

链路缺三环，须同时补齐：

1. **身份源**：网关 `/api/members/me` 返回 member 的 team/project（`members_api.rs:577-583`）；
2. **成员关系**：`team_membership`/`project_membership` 真实落行（当前两表 **0 行**，
   故 `can_access_resource` 永不命中 team/project 分支）；
3. **上游策略**：允许 `imt_` 调用方声明 team/project 并**校验归属**；ownership 侧
   对 team/project 做 clamp/查表（消除 B 的孤儿库语义）。

**裁决：本期暂不实现。** 该能力跨 intellect-team / intellect-rag-app / openkg-webui
三仓库，且第一个前置（网关返回 team/project）不在本项目仓库内。登记见
`docs/knowledge-center-port-design.md` §十三。

本期采取的产品面兜底（已落地）：**不承诺**该能力——KC 状态端点返回
`create_visibility` 缺失或为 `private` 时，创建对话框**陈述**"仅你自己可见"，
创建接口把 `team` 钳制为 `me`，徽标只反映真实的 `visibility`。因此 P2 未实现**不会**产生
错配数据，只是少一个功能入口。待三环补齐后，能力位自动翻转，前后端无需再改。

## 四、风险与不做的事

- **不使用服务 key 替代 token**：服务 key 是无限制主体，等于放弃隔离。
- **不使用 `scope=all`**：实测 `101 requires a trusted integration context`（member token 被拒），
  且其语义是全局视图，与 per-user 隔离冲突。
- **不改** `identity.py` 的 token 模式行为（turn 路径），仅 KC 代理层补头。
- P0-A 跨仓库，需 rag-app/rag 重启与部署文档同步（`docs/knowledge-center-deployment.md`）。

## 五、决策记录

| # | 决策点 | 决策 |
| --- | --- | --- |
| D1 | 租户并集口径 | **采纳**（对齐 `get_accessible_ids_by_scope` 既有口径，同文件复用 `get_joined_tenant_ids`） |
| D2 | 修 `filter_kb_rows_by_scope` 一处 vs 改各调用点 | **采纳前者**（覆盖 4 个入口，其中 `dataset_api.py:490`/`dataset_api_service.py:1460`/`chunk_api.py:311` 的 `subject_context is None` 分支实为死代码——装饰器恒注入） |
| D3 | token 模式补 `X-Intellect-User` | **采纳**（仅 KC 代理层；依赖上游主体钳制兜底） |
| D4 | 团队可见性 | **P1 先禁用入口**（阻断孤儿库），实现与否留 P2 决策 |
| D5 | 前端徽标数据源 | **采纳 `visibility`**（`permission` 是 legacy 且不参与鉴权） |
| D6 | 能力门控的数据来源 | **采纳运行时推导**（status 端点由"实际会发的归因头"算出 `create_visibility`，P2 打通后自动放行，前后端都不必再改）。**修订（2026-09-25 评审后）**：改为返回**范围字符串**而非布尔——上游两个方向都不可选，UI 应陈述而非让用户选 |
| D7 | 创建时是否钳制 `permission` | **采纳钳制**（身份不支持时把 `team` 写回 `me`；否则留下 legacy 说 team、visibility 是 private 的错配记录） |

## 六、实施记录（2026-09-25）

### 6.1 落地内容

| 项 | 位置 | 内容 |
| --- | --- | --- |
| P0-A | `intellect-rag/api/db/access_control.py` `filter_kb_rows_by_scope` | 租户集合改为 `get_joined_tenant_ids(acting_user) ∪ 上下文租户`；查询失败仅告警并退回上下文旅户（fail-closed 不抛错、不放宽）。覆盖 4 个入口（列表 / `datasets/search` / `retrieval` 显式 ids / `get_accessible_ids_by_scope`），**1 处修改** |
| P0-A 测试 | `intellect-rag-app/test/unit_test/test_access_control.py` | 新增 3 例：并集生效、并集不放开他租户 private、查询失败退回上下文 |
| P0-B | `openkg_webui/services/knowledge/__init__.py` `resolve_request_auth` | token/token_required 模式补发 `X-Intellect-User = member_id`（已有同名单头时不覆盖） |
| P0-B 测试 | `tests/services/knowledge/test_request_auth.py`（新增） | 6 例：token/token_required 补发、header 模式不回归、不覆盖既有头、member_id 缺失不写空头、无 bearer 仍报身份不可用 |
| P1 | `engines/models.py` + `engines/intellect_rag.py` | 域模型新增 `visibility`；`_dataset_visibility()` 归一（优先权威列，legacy `permission=team` → `tenant`，未知 → `private`） |
| P1 | `api/routers/knowledge.py` | status 端点新增 `create_visibility`（范围字符串）；创建时按之上报/钳制 `permission`；非法值 400 |
| P1 | `web/features/knowledge/model.ts`、`KnowledgeHomePage.tsx`、`api.ts`、`useKnowledgeStatus.ts` | 徽标改读 `visibility`（private/tenant/team/project）；创建对话框的团队选项按能力禁用并给说明；未知值保守显示 private |
| P1 i18n | `web/locales/zh/app.json` | 新增 Tenant / 能力不可用说明 / 团队共享前置条件 3 条 |
| P1 测试 | `web/tests/knowledge-parse.spec.ts`、`tests/api/test_knowledge_router.py` | 徽标数据源与归一 2 例；status 能力位、visibility 归一、创建钳制 2 例 |

### 6.2 实机验收（本机部署）

```
列表（KC → 上游）        修复前 1 条  →  修复后 3 条（1 private + 2 tenant）
同一问句检索 scope=tenant 修复前 8 chunks(1 库) → 修复后 12 chunks(2 库，含租户级)
显式 dataset_ids 选租户库  此前 denied → 不再 denied（denied_dataset_ids 为空）
三条库逐条可召回          4a58「测试」✓ / 9a5c「场景图」✓ / 73df「PPPINSLiDAR」✓
分页不变量               page_size=2 → 第1页2条 + 第2页1条，total 恒为 3
KC 状态端点              {"enabled":true,"identity_ok":true,"create_visibility":"private"}
非成员隔离              some-other-user → [] ；
                       0554315eb565（确为该租户成员）→ 可见 2 条 tenant 库 ✓ 符合租户语义
```

> 注：KC 端到端（`GET /api/knowledge-center/datasets` → 3 条）已验；**浏览器 UI 未目视
> 验收**——本会话无可用浏览器后端（`agent.browsers.list()` 返回空）。已改为验证
> 生产构建产物包含新逻辑（`create_visibility` 与徽标映射进入客户端 chunk），
> 加上 tsc/vitest 通过。目视复核建议在上线前手工过一遍。

### 6.3 实施中发现的两处修正（对 §二/§三 的更正）

1. **"检索 scope 路径已正确"的判断错误**（§二-A 已就地更正）。实测 `scope=tenant`
   同样只召回私有库：`get_accessible_ids_by_scope` 的 `joined` 集合**只用于取数**，
   未传给过滤函数；过滤函数只认 `subject_context.groups`。故检索面与列表面同因，
   同属 P0-A 修复范围。
2. **P0-B 的必要性有前提，但不影响采纳**。补发主体头只在"上游主体 ≠ 成员 id"
   时才改变结果——即部署设置了 `INTELLECT_TENANT_ID`、或调用方带
   `X-Intellect-Tenant` 的场景。本机为 personal-tenant（tenant == user）故无差异；
   但该改动消除了主体身份被请求头左右的路径，属纵深防御，保留。

### 6.4 部署注意（行为扩张）

`visibility=tenant` 行现在会出现在**该租户全部成员**的列表中（此前"可按 id
访问但不列出"）。本机 `0000…0000` 租户有 14 个成员，含 `test-member`、
`mem_check` 等测试账号——这 2 条库对他们即可见。这是租户可见性的应有语义，
但上线前应审计 `visibility=tenant` 的存量行与其租户成员名单。

### 6.5 回归结果

- `intellect-rag-app`：`test_access_control.py` + `test/unit_test/api` → **194 passed**
- `openkg-webui` 后端：`tests/` → **1740 passed / 23 failed 全部为预存失败**，
  已用 `git stash` 在干净基线上逐一复现（改动前同样失败，且均与 knowledge 无关）：
  `test_release_workflow_guards.py` 21 项（release tag 守卫）、
  `test_codebuddy_provider.py` 2 项（CodeBuddy SDK 桩）。
  **后续（2026-09-26）**：CodeBuddy 2 项已定位并修复（见 §九）；release 守卫 21 项
  确认为本 fork 无 `.github/workflows/` 所致，待裁决（见 §九末）。
- `openkg-webui` 前端：vitest **61 passed**（15 文件）、node **690 passed**、
  `tsc --noEmit` 干净、`i18n:parity` + `i18n:audit` 通过

## 七、未完成任务清单（2026-09-25 结项时点）

### A. 本期范围内、已实现但与"完全闭合"有差距的项

| # | 项 | 状态 | 缺口 / 待办 | 归属 |
| --- | --- | --- | --- | --- |
| A1 | 浏览器 UI 目视验收 | **未做** | 本会话无可用浏览器后端（`agent.browsers.list()` 返回空）。已用替代证据：KC 端到端返回 3 条、生产构建产物含新逻辑（`create_visibility` + 徽标映射进入客户端 chunk）、tsc/vitest 通过。**上线前需手工过一遍列表徽标与创建对话框的范围陈述** | openkg-webui |
| A2 | run 级端到端身份传导验证 | **未做** | 网关侧修复（`9cb74bc4`/`2dc4cacd`）已部署，但未实际发起 runs 并核对 rag-app 收到的 `X-Intellect-User`。建议发起一次 `scope=tenant` 的会话轮次，在 rag-app 日志确认归因头为发起用户 | intellect-team |

### B. 本期裁决不做（已登记 TODO）

| # | 项 | 登记位置 |
| --- | --- | --- |
| B1 | **团队/项目维度打通**（网关返回 team/project + 成员关系落行 + 上游校验归属）——本期裁决不实现 | `docs/knowledge-center-port-design.md` §十三（含 4 条子项与验收标准） |
| B2 | 能力门控自动翻转 | 无需单独实施——`create_visibility` 由运行时归因头推导，B1 补齐后自动变为 team/project |

### C. 部署侧待办（非代码）

| # | 项 | 说明 |
| --- | --- | --- |
| C1 | `visibility=tenant` 存量行审计 | 修复后这些行会对**该租户全部成员**列出。本机 `0000…0000` 租户 14 个成员含 `test-member`/`mem_check` 等测试账号，需确认符合共享预期 |
| C2 | 三仓库提交 | `openkg-webui`（12 改 2 增，分支 `feature/knowledge-center`）、`intellect-rag`（`main`）、`intellect-rag-app`（`simon`）均未提交。后两者直接落在 `main`/`simon` 上，建议开修复分支再提交 |

### D. 本次顺带复核并关闭的旧遗留（原记载已过期）

| # | 项 | 结论 |
| --- | --- | --- |
| D1 | MCP server 归因限制（`tools/list` 恒 401） | **已修复**（`62a472a`/`349dc1e`）。实测带 `X-Intellect-User` 返回工具定义，不带头 401（fail-closed 正确） |
| D2 | `ensure_team_user` 租户兜底（102 Tenant not found） | **已修复**（`9e855b4`）。解析不到租户时回落 `member_id`，并幂等 upsert tenant + tenant_membership |
| D3 | 网关检索身份传导 | **已实现**（`9cb74bc4`/`2dc4cacd`），但未做端到端验收（见 A2） |

### E. 与本方案无关的预存问题（不在本期范围，仅供后续参考）

| # | 项 | 说明 |
| --- | --- | --- |
| E1 | `tests/test_release_workflow_guards.py` 21 项失败 | release tag 守卫，改动前后一致失败（已 `git stash` 基线复现），零引用 knowledge |
| E2 | ~~`tests/services/llm/test_codebuddy_provider.py` 2 项失败~~ | **已于 2026-09-26 修复**（见 §九）；根因为测试字面量与 provider 的 server 名不符，非 SDK 环境问题 |

## 八、质量与安全评审及修复（2026-09-25）

对本次全部改动做了对抗性评审（独立评审者静态分析 + 本机实证测试，含真实 DB 与
运行中服务）。结论：**方向正确、未引入新的越权路径**，但发现 1 个 P0 相邻缺陷与
若干需修项。以下为修复记录。

### 8.1 P0：伪造 `X-Intellect-Tenant` 可自加入任意租户（实证，且先于本次改动存在）

评审中做强头部测试时发现并确认。**证据**（本机实测，实验行已清理）：

```
实验前 testreview01 中 local-admin 的成员行: []
发起一次请求（合法 imt_ 令牌 + X-Intellect-Tenant: testreview01）
实验后 testreview01 中 local-admin 的成员行: [('local-admin', 'normal')]
→ 之后不带任何头，该租户 tenant 级知识库持续可见（成员行持久）
```

**责任归属**（对照实验）：变更前"已自加入 + 带伪造头"即可见——缺陷**先于本次改动存在**；
本次并集修复使它持久化且无需重复带头。

**根因**：`X-Intellect-Team/Project` 有显式防护（`imt_` 路径不接受），但 `tenant_id`
走 `resolve_tenant_id`，而 `imt_` 令牌算集成请求、该函数会采纳头。**同一威胁模型漏了一维。**

**修复**（三维现在是同一条规则）：

| 文件 | 改动 |
| --- | --- |
| `api/utils/tenant_utils.py` | 新增 `resolve_tenant_id_for_token_caller()`：只认 env 覆盖与令牌自身 member_id，不读头 |
| `api/utils/sync_membership.py` | 按凭据分流：服务 key 走原解析器，成员令牌走新函数 |
| `api/db/services/user_service.py` | `ensure_team_user` 新增 `trust_request_tenant_header: bool = False`；头解析仅在为 True 时进行 |
| `api/apps/__init__.py` | 服务 key 调用点传 `True`（网关可信）；`imt_` 调用点保持默认 `False` |
| `test_sync_membership.py` | 新增自加入回归用例 + env 覆盖用例；2 例改为新契约 |

**修复后实测**：同一攻击请求 → `testreview01` 中 `local-admin` 行数为 **0**；
KC 列表仍 3 条、`local-admin` 成员关系仍 2 条（正常路径无回归）。

### 8.2 P1：能力门查错头 + "私有"方向无兜底 → 改为陈述范围

原 `_team_visibility_available` 只查 `X-Intellect-Team`，而上游 `_compute_visibility`
是三分支（team → project → private）。两处后果：

- **漏判**：project-only 身份被报 `false`，UI 禁用唯一共享选项且文案说"缺少团队关系"；
- **更严重**：已验证 `ownership.py` 完全不含 `permission`、`CreateDatasetReq` 无 `visibility`
  字段 → **有 Team/Project 头时"仅自己可见"必然失效**（选私有却建成共享）。

**修复**：`create_visibility` 返回**范围字符串**而非布尔（上游两个方向都不可选，
所以 UI 陈述而非让用户选）：路由层 `_visibility_from_headers()`；创建时据之上报
`permission`；前端对话框改为展示徽标 + 一句说明，请求体回传与之一致的值。

### 8.3 其余修复

| # | 项 | 修复 |
| --- | --- | --- |
| P1-2 | `mem_` 命名空间（评审列为"需测试"） | **核实为非缺陷**：rag-app 解析出的主体是 `verify_member_token`（→ 同一 `/api/members/me`）的返回值，与 KC 补发的 `member_id` 同源，必然一致；上游另有 fallback owner 短路兜底。已在代码注释写明 |
| P2-2 | `except Exception` 被称作 fail-closed | 改为准确表述 **fail-narrow**，并说明为何不收窄到空集（列表与检索共用，DB 抖动时整页消失更难排查）；移除已不准确的 `# pragma: no cover` |
| P2-3 | `permission=team → tenant` 忽略 `team_id`（比实际更宽） | 先看 `team_id`/`project_id` 再回退 permission；补 3 条测试 |
| P2-4 | `bool(engine._auth())` 恒为 True；身份重复解析 | 去掉真值化并写明原因；`_visibility_from_headers(headers)` 改为接收 headers，两条路径各只解析一次 |
| P3-1 | i18n 键冲突（`Project` 是设置页标题） | 改用独立词条 `Project only`；标签改为 `switch` 字面量，使 i18n 审计可见 |
| P3-2 | 契约产物漂移且无守卫 | 重新生成 `web/contracts/schema/openapi.json`（含 `visibility`）；新增 `test_committed_contract_files_match_the_renderer` —— **已人为制造漂移验证它会失败** |
| P3-3 | 文档声称 token 模式恒用 member token | 补注降级形态（未链接 → 服务 key、`degraded=True`） |
| P3-4 | 非法 `permission` 被静默折叠成 `me` | 改为 400，与上游 `Literal` 同口径 |
| P3-5 | 头字面量重复无守卫 | 新增 `test_header_literal_matches_identity_module`；补注为何 KC 要补回 identity.py 有意丢弃的头 |

### 8.4 未采纳 / 未修

| 项 | 处置 |
| --- | --- |
| P2-1 并集激活 `fallback_user_id` owner 短路（可绕过 `visibility=private` 让同租户成员读到组织行） | **确认为既有设计意图**（该短路就是为 pre-P0-1 的 RAG-UUID 归属行而设），本机 0 行命中。属"放宽但无测试"，建议后续补一条断言；本轮未改行为 |
| 测试 `_filter` 把 `can_access_resource` patch 成 `True` | 属既有夹具设计，本轮未改；新增的自加入回归用例走真实 gate |

### 8.5 回归结果（修复后）

- `intellect-rag-app`：`test_access_control.py` + `test_sync_membership.py` + `test/unit_test/api`
  → **226 passed**
- `openkg-webui` 后端：**1747 passed**（当时 2 项 CodeBuddy 为预存失败，已基线复现；
  后于 2026-09-26 修复，见 §九）
- 前端：tsc 干净、vitest 61 passed、node 690 passed、i18n parity + audit 通过
- 实机：状态端点 `{"enabled":true,"identity_ok":true,"create_visibility":"private"}`；
  列表 3 条；非法 permission → 400；攻击请求不再写入成员行

## 九、CodeBuddy 预存失败修复（2026-09-26）

### 9.1 现象与根因

`test_codebuddy_provider.py` 2 项失败（改动前后一致）：

```
test_codebuddy_provider_maps_sdk_mcp_tool_calls      assert 'stop' == 'tool_calls'
test_codebuddy_session_drains_interrupt_before_...   IndexError: tool_calls[0]
```

两者同因：provider 未能从 SDK 回传的 tool_use 块识别出工具调用，于是
`finish_reason` 落成 `stop`、`tool_calls` 为空。

**根因是名字不一致，不是 SDK 环境问题**（此前 §7 E2 记为"SDK 桩"，不准确）：

| 位置 | 值 |
| --- | --- |
| provider `_MCP_SERVER_NAME` | `openkg-webui`（连字符） |
| provider `_MCP_TOOL_PREFIX` | `mcp__openkg-webui__` |
| 测试字面量（3 处） | `mcp__openkg_webui__web_search`（下划线） |

`_assistant_tool_calls` 用 `name.startswith(_MCP_TOOL_PREFIX)` 判定，测试喂入的名字
前缀不匹配 → 直接 `continue` → 无工具调用。

### 9.2 判定哪一侧是对的（而非改测试让它变绿）

**证据链**（三条独立证据，指向同一结论）：

1. **上游对照**：本 fork 派生自 DeepMentor。其 `_MCP_SERVER_NAME = "deepmentor"`，
   测试期望 `mcp__deepmentor__web_search`——server 名与测试一致，且该仓库这两个
   测试**实测 10 passed**。移植时把字面量改成了**包名**（`openkg_webui`），而
   provider 的 server 名用的是 `openkg-webui`，改漏了一侧。
2. **SDK 源码**（下载 `codebuddy-agent-sdk==0.3.263` 核对）：`create_sdk_mcp_server`
   把 `name` 原样存入配置（`{"type": "sdk", "name": name, ...}`），其自身 docstring
   示例即 `name="my-server"`（连字符）。CLI 二进制内工具名按
   `mcp__${serverName}__${toolName}` **逐字拼接**，不做规范化。
3. **测试自相矛盾**：同一用例 line 190 期望 `mcp__openkg_webui__...`，line 191 断言
   `"openkg-webui" in option_kwargs["mcp_servers"]`——既然服务器名是连字符，前缀就不
   可能是下划线。该断言在修复前**必然不可同时成立**。

补充旁证：本项目自己的 KAG 集成文档记录的实测工具名是
`mcp__kag-bridge__kag_solve`（同为连字符 server 名）。

**结论**：provider 正确，测试字面量错误。修测试。

### 9.3 修复内容

| 项 | 改动 |
| --- | --- |
| 测试字面量 3 处 | `mcp__openkg_webui__web_search` → `mcp__openkg-webui__web_search` |
| 新增契约测试 | `test_codebuddy_advertised_tool_names_round_trip_through_the_parser` |

新增测试锁的是**两侧同源**这一不变量：`_build_tool_options` 放行的名字必须能被
`_assistant_tool_calls` 解析回原工具名。断言写成
`f"mcp__{server_name}__web_search"` 而非硬编码字面量，因此改常量不会误报，
只改一侧才会失败。已验证：把 `_MCP_SERVER_NAME` 改成 `renamed-server`（两侧同源）
时该测试仍通过，而 2 个硬编码字面量的用例如预期失败——即它拦得住"改漏一侧"。

### 9.4 验证

- `test_codebuddy_provider.py`：**11 passed**（原 10 项含 2 失败 + 1 新增）
- 全仓库后端：**1750 passed / 21 failed**——仅剩 release 守卫

### 9.5 剩余 21 项：本 fork 无 `.github/workflows/`（待裁决，未修）

`test_release_workflow_guards.py` 失败于：

```
FileNotFoundError: '.github/workflows/docker-release.yml'
```

本仓库**根本没有 `.github/` 目录**（`git log --all -- .github/workflows/` 无任何历史），
而上游 DeepMentor 有 `docker-release.yml`/`pypi-release.yml`/`tests.yml` 等。即这些
守卫在断言**本 fork 从未移植的发布基础设施**。

**这不是代码缺陷**，两种处置各有代价，需owner决定（本轮未擅自处理）：

| 选项 | 影响 |
| --- | --- |
| 移植上游 workflow | 恢复 21 项为有效断言，但需一并承担 CI 发布配置的维护 |
| 给守卫加 skip（无 workflow 则跳过） | 消除噪音，但等于承认本 fork 不做 CI 发布——守卫从此对本仓库无意义 |
| 删除该测试文件 | 最干净，但若日后移植 workflow 需重建断言 |

## 十、CI 脚本补齐（2026-09-26）

补上 `.github/workflows/`（原 §九 5 记录的 21 项守卫失败即因该目录不存在）。移植自上游
DeepMentor，按本 fork 实际结构适配。

### 10.1 新增文件

| 文件 | 作用 |
| --- | --- |
| `.github/workflows/docker-release.yml` | Release 发布时构建多平台镜像推 GHCR；`validate-release-tag` 门 + `build-and-push` |
| `.github/workflows/pypi-release.yml` | Release 发布时构建 sdist/wheel 发 PyPI（Trusted Publishing）；`validate-release-tag` 门 + `build-and-publish` |
| `.github/workflows/tests.yml` | lint（ruff + import-linter + 架构边界）/ web-tests（`npm run check`）/ import-check（3.11–3.14 × Linux/macOS/Windows）/ python-tests / test-summary |
| `.github/workflows/repository-hygiene.yml` | 仓库卫生（跟踪生成物 + 工作区洁净） |

**上游适配点**（非照抄）：

- 包名 `deepmentor`→`openkg_webui`、镜像 `ghcr.io/hkuds/deepmentor`→`ghcr.io/<repo owner>/openkg-webui`
  （用 `github.repository_owner`，换组织/复刻无需改文件）。
- **移除工具层断言**：上游 import-check 含 `tool_registry`，而本 fork 按
  `AGENTS.md` 已删除工具层（`ModuleNotFoundError` 实测确认），照抄会让 CI 必红。
- **PyPI 只发布 `openkg-webui`**：上游另发 CLI 包；本 fork 的 CLI 是同一源码树的窄依赖
  视图（`packaging/openkg-webui-cli`），两包共用 `openkg_webui/__version__.py`，再发一个
  PyPI 项目需另行配置 trusted publishing，故暂不发，文件头注明本地安装方式。
- **不强制 `ruff format --check`**：实测该命令在**上游**即为红（43 个 Python 文件），
  继承到本 fork 后 gate 每次必红且与评审无关（见 10.3）。
- `tests.yml` 去掉上游的 `multi-worker-web`（依赖外部 `vars.DEEPMENTOR_*` 部署夹具）与
  Playwright 审计段（需起服务 + 浏览器矩阵，本 fork 无对应夹具）。

### 10.2 顺带修复的 4 个真实缺陷（接入 CI 才暴露）

`ruff check` 首次运行报 50 项，其中 46 项是机械 import 排序，**4 项是真问题**，
且经 `git stash` 基线确认为**改动前既有**：

| # | 位置 | 问题 | 处置 |
| --- | --- | --- | --- |
| 1 | `api/routers/knowledge.py:49` | `httpx.AsyncBaseTransport` 未导入 `httpx`。因 `from __future__ import annotations` 使注解延迟求值而未爆，但是隐患——去掉该 import 即 `NameError` | 模块级导入 `httpx` |
| 2·3 | 同上 `:641`/`:816` | `engine_id_for_dataset` 只在 `_engine_for` 内导入，却在 `_run_github_sync`/`_run_web_sync` 中使用 → **两处 `NameError`**（AST 作用域分析实证：局部可见=否） | 提升为模块级导入 |
| 4 | `engines/intellect_rag.py:378` | 两个同名 `upload`：T2 版（返回 `httpx.Response`）被 T3 版（返回 `UploadResult`）覆盖，成不可达死代码并触发 F811 | 删除 T2 版，留注释说明 |

缺陷 2·3 意味着**GitHub/Web 外部源同步路径此前必然抛 `NameError`**——它们没有测试覆盖，
所以一直未被发现。这是"接入 CI 后立刻还清的技术债"，不只是格式问题。

### 10.3 另外两处被 CI 暴露的陈旧配置

| 位置 | 问题 | 处置 |
| --- | --- | --- |
| `.importlinter` | `root_package = openkg-webui`（连字符）。import-linter 按模块路径解析，实测直接 `Could not find package 'openkg-webui'`——**lint job 一跑就红** | 改为 `openkg_webui`；实测 3 条契约 0 broken |
| `web/scripts/route_budgets.mjs` | `/kag*` 预算 360KB，实测 690KB 全红。作者注释已写明"M3 图可视化落地时需按实测上调"，但 M3.4 引入 cytoscape 后漏做 | 按实测上调至 780KB（参照同为图页的 `/chat/[sessionId]` 682KB/1020KB，留 ~90KB 余量） |
| `web/contracts/generated/api.ts` | 先前重新生成 `schema/openapi.json`（补 `visibility`）后未同步生成物，`contracts:check` 失败 | `npm run contracts:generate` 重生成 |

### 10.4 验证（各 CI job 命令本地实测）

```
ruff check .                     → All checks passed
lint-imports                     → Contracts: 3 kept, 0 broken
scripts/check_architecture.py    → Architecture boundaries: OK
scripts/check_repo_hygiene.py    → Repository hygiene check passed
import-check 五条 import 命令    → 全部 ✅（不含工具层，符合本 fork 设计）
npm run check（web-tests job）    → EXIT=0（contracts/架构/类型/node/unit/lint/i18n/build/预算）
pytest tests/                    → 1771 passed, 6 skipped, 0 failed
```

**后端测试首次全绿**：原 23 项预存失败中，CodeBuddy 2 项于 §九修复，release 守卫 21 项由
本节补齐 workflow 后转绿。

### 10.5 遗留：本仓库远端是 Gitee，Actions 不会运行

`git remote -v` 显示 `origin = https://gitee.com/wustbd/openkg-webui.git`。GitHub Actions
在 Gitee 上不执行，因此：

- 这四个 workflow **当前的可执行价值是"可移植的发布规程 + 被契约测试锁定的校验逻辑"**
  （`tests/test_release_workflow_guards.py` 21 项直接执行 workflow 里的校验脚本），
  而非实际 CI 运行。
- 若要在 Gitee 上真正跑 CI，需迁到 Gitee Go（`.workflow/` 或网页配置），并重新实现
  同样的 tag 校验；`pypi-release.yml` 的 GHCR/PyPI 集成也需替换为 Gitee 对应制品库。
- 文件头已注明该现状，避免后来者误以为推送即触发。

## 十一、启用 ruff format 门（2026-09-26）

§十 曾刻意不启用 `ruff format --check`（上游自身即红 43 个文件）。本轮清偿：

- **sweep**：`ruff format .`（ruff==0.16.0，与 CI/pre-commit 同版本）重排 57 个文件
  （52 py + 5 md；md 指 fenced python 代码块，pre-commit 的 ruff-format hook 本就是
  该口径）。清单按目录核对过，**不含任何测试夹具**——不存在"故意写坏"被格式化的风险。
- **验证**：`ruff format --check .` 652 files already formatted；`ruff check .` 仍全过；
  `pytest tests/` 1771 passed——格式化行为中性。
- **启用门**：`tests.yml` lint job 恢复 `ruff format --check .` 步骤；
  CONTRIBUTING 的「刻意空缺」清单相应收敛为一条（Gitee 不跑 Actions），
  本地复现命令加入 `ruff format --check .`。
- 与 pre-commit 的版本钉（ruff-pre-commit v0.16.0）一致，本地 hook 与 CI 不会因
  版本差出现「本地过、CI 红」（`.pre-commit-config.yaml` 注释里记载的历史教训）。

至此 §10.4 列出的 lint job 全部步骤（ruff check + ruff format --check +
lint-imports + check_architecture）本地均可复现通过。
