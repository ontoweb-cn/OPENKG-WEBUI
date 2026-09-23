# 知识中心部署与配置指南

- 更新：2026-09-22（Phase 1b）
- 适用：openkg-webui（知识中心 + chat 召回接线）× intellect-rag-app（:9380）× intellect-team 网关（:9091）
- 依据：[knowledge-center-port-design.md](knowledge-center-port-design.md) §三/§五/§九/§十一

## 1. 组件与端口

```
openkg-webui
  ├─ API      :8082（/api/knowledge-center 代理、/api/settings/knowledge）
  └─ Web dev  :3300（生产用 openkg-webui start）
intellect-rag-app :9380   知识库权威数据 + 检索（知识中心后端）
intellect-team 网关 :9091  openkg-webui runs 的实际服务者（Rust gateway）
MCP :9382 / Admin :9381 / MEM :9383 / Task Executor（无端口，Redis 心跳）
基础设施：Postgres、Redis、Elasticsearch、MinIO（docker compose）
```

## 2. openkg-webui 侧配置（Settings → 设置 → 知识中心）

| 配置 | 值 | 说明 |
| --- | --- | --- |
| 启用知识中心 | on | 控制侧栏「知识库」入口与 API 门控 |
| Intellect RAG server URL | `http://127.0.0.1:9380` | rag-app API 地址 |
| API key | 与 intellect-team 的 `INTELLECT_RAG_API_KEY` **同一把** | 两处不一致会导致两套自动建户身份（设计 §十一-2） |
| 聊天召回范围 | `tenant`（默认）/ team / project / auto | 随 runs 请求体的 `rag.scope` 下发（Phase 1b T1） |

身份说明：聊天与知识库管理共用 agent-loop 的身份解析（`resolve_backend_identity`）。`header` 模式 = 服务 key + `X-Intellect-User` 归因；`token` 模式 = 用户 member token 委托（隔离更强）。账号需在 `/settings/agent-loop` 完成 Intellect 身份链接后才能获得团队级可见范围。

落盘位置：`data/user/settings/system.json` 的 `knowledge` 块（`enabled/base_url/api_key/chat_scope`）。

## 3. intellect-team 网关侧

- 环境：`RAG_SERVICE_URL` 指向 rag-app；`INTELLECT_RAG_API_KEY` 同一把 key。
- `~/.intellect/config.yaml` 的 `rag:` 块：prefetch 开关/关键词；**scope/kb_ids 不在此配置**——openkg-webui 的 runs 请求体现在直接携带 `rag` 块（Phase 1b T1）：

```json
{ "input": "...", "session_id": "...", "rag": { "enabled": true, "scope": "tenant" } }
```

网关 `build_session_config` 契约：`rag.enabled`（缺省启用）、`rag.knowledge_base_ids`（Phase 1.5 的 per-会话库选择）、`rag.scope`（非法值忽略）。`rag.scope` 语义见知识中心设置的「聊天召回范围」。

## 4. 已知坑（Phase 1a/1b 联调实录）

1. **"102 Tenant not found"**：rag-app 服务 token 路径的自动建户（`ensure_team_user`）在无法解析租户（无 X-Intellect-Tenant header 且无 `INTELLECT_TENANT_ID` env）时只建用户不建租户，且对已存在用户永不补建 → 首次建库 102。处置：为对应用户补 tenant + tenant_membership 行（可参照 `tenant_model_service` 三表一并配 embedding/provider）。**遗留**：`ensure_team_user` 应回落 member_id 作为 per-user 租户并对已存在用户幂等补建（intellect-rag-app 变更，待提）。
2. **新租户缺 embedding/provider 配置**：自动建户的租户 `embd_id` 为空、无 `tenant_model_provider/instance/model` 行 → 解析报 "Fail to bind embedding model: Provider … not found"。处置：克隆一个正常租户的三表行并设置 `tenant.embd_id`。
3. **/retrieval 401**：rag-app 的服务 token 路径要求 `X-Intellect-User` 头（缺失即 401）——直连 curl 不带头必然 401，属预期；调用方（网关/webui）必须携带成员身份。
4. **上传字段名**：rag-app 文档上传 multipart 字段为 `file`（单数），另有可选 `type=local` 与 `parent_path`。

## 5. 运维与迁移（2026-09-22 执行记录）

### 5.1 标准启动 / 重启

```bash
# 全栈（推荐）：基础设施 + API/Admin/MCP/MEM/Task Executor
cd ~/projects/intellect-rag-app && bash scripts/start-stack.sh start      # 或 restart --skip-infra
bash scripts/start-stack.sh status                                        # 端口 + 进程 + 心跳

# openkg-webui（生产形态，detached 常驻）
cd ~/projects/openkg-webui && .venv/bin/openkg-webui start --detach --no-browser
.venv/bin/openkg-webui stop    # 停止
```
生产形态端口：后端 **8082**、前端 **8092**（dev 模式为 3300）。前端首次为生产构建，需数分钟。

### 5.2 存量知识库归属迁移（Phase 1.5 R1）

工具：`intellect-rag-app/scripts/migrate_kb_tenant.py`（dry-run 默认）。本次已修复其 4 处与当前引擎 API 的漂移（`api.db.DB` → `api.db.db_models.DB`、`tenant_service` 与 `get_xor_fields` 不存在的导入、`DB.init_env()` 不存在）。

**执行方式**（本仓库 `fix/team-user-tenant-fallback` 分支已含修复）：

```bash
cd ~/projects/intellect-rag-app
PYTHONPATH=".:../intellect-rag:../intellect-mem:../intellect-dsl" DB_TYPE=postgres \
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_USER=intellect_rag \
POSTGRES_PASSWORD=<pwd> POSTGRES_DB=intellect_rag .venv/bin/python scripts/migrate_kb_tenant.py
# 加 --apply --to <目标租户> 才真正执行；执行前先停 task_executor
```

**2026-09-22 dry-run 影响报告**（本实例）：

| legacy 租户 | KB | chunks | owner |
| --- | --- | --- | --- |
| `2d0b100f273a` | AI技术 | 91 | 2d0b100f273a |
| `2d0b100f273a` | probe-no-tenant-header / probe-with-tenant-header | 0 / 0 | 2d0b100f273a |
| `local-admin` | 联调测试库(1) | 7 | local-admin |

共 4 个 KB / 98 chunks。**注意**：迁移会把这些 KB 的 `tenant_id` 改为目标租户并重写 ES 索引；private 库迁移后仍不对他人可见，但**所属租户改变会影响租户级 scope 的可见集**。执行前确认影响面（尤其 `2d0b100f273a` 的 `AI技术` 是真实知识库），并停止 TE。

##### 5.3.1 租户迁移回滚记录（2026-09-22，重要）

**结论：本实例不做租户归一；已执行的迁移已回滚。**

执行后发现的功能破坏（实测）：迁移把 KB 移到默认租户后，**上传路径**的
`check_kb_team_permission`（`api/common/check_team_permission.py`）以登录租户
严格匹配 KB 租户——token 身份下登录租户为 `local-admin`（personal-tenant 模型，
tenant==user），与 KB 的新租户 `default` 不匹配 → 上传返回
`109 No authorization`。列表/检索/删除不受影响（走 `can_access_resource` 的
owner 短路与宽松检查）。

回滚内容（PG 与 ES 同步，含 file / pipeline_operation_log 行）：
- `AI技术` → `2d0b100f273a`
- `联调测试库(1)` → `local-admin`

**后续若确需归一租户**，前置条件是先修 `check_kb_team_permission`（改为按
owner 归属或接受用户自租户），否则管理面上传会静默失效。迁移工具的
`--grant-membership` 与 file/log 同步已实现（见 `scripts/migrate_kb_tenant.py`），
但该权限语义问题使其在当前 personal-tenant 部署下不可用。

### 5.3.2 解析日志流（T3）生产验证记录（2026-09-22）

- 端点：`GET /api/knowledge-center/datasets/{id}/logs/stream?max_ticks=N`
- 实测：订阅 60s 收到 2 个 `data:` 帧（含 `progress_msg` 逐行解析日志与
  `progress=1.0`）+ 8 个 keepalive 保活帧；上传新文档后日志随 ingestions
  快照变化推送。
- 结论：SSE 日志流在生产实例工作正常；前端 `KnowledgeDetailPage` 在解析进行时
  自动订阅（`EventSource(logsStreamUrl(id))`），闭环可用。

## 5.3 身份委托（D1=A）启用记录 + TEAM 端口坑

**已启用**（2026-09-22）：local-admin 已签发 `imt_` 成员令牌并完成身份链接
（`data/system/user-secrets/local-admin/private/intellect-agent/credentials.v1.json`），
profile 切至 `identity_mode: token`。**验证结论**：token 模式下管理面建的库
owner=local-admin，聊天面经令牌委托可检索同一库——建/检同源成立。

**关键坑（已修）**：`intellect-rag/docker/.env` 的 `INTELLECT_TEAM_API_URL`
仍指向**旧端口 8642**，而 intellect-team 网关已迁至 **9091**（其仓库提交
「API server 端口 8642 → 9091」）。后果：rag-app 无法验证任何 `imt_` 令牌
（全部 401），token 委托链路整体不可用。修正为 `http://127.0.0.1:9091` 并重启
rag-app 后恢复正常。**部署时务必核对两侧端口一致。**

**令牌管理**：member API 令牌存 `intellect` 库的 `member_api_tokens` 表
（sha256 哈希，无法反查明文）。重新签发：
```sql
INSERT INTO member_api_tokens (id, member_id, name, token_hash, scope_type, created_at)
VALUES (substr(md5(random()::text),1,32), '<member_id>', 'openkg-webui',
        encode(sha256('<imt_令牌>'::bytea), 'hex'), 'member', <epoch_ms>);
```
验证可用性：`curl -H "Authorization: Bearer <令牌>" http://127.0.0.1:9091/api/members/me`。

## 5.3 MCP server 归因限制（已知缺陷，2026-09-22 发现）

**现象**：`mcp/server/server.py` host 模式下，服务 key + `X-Intellect-User` 的客户端在 `tools/list` / 工具调用时收到 **401**。
**根因**：server 只提取 token（`_extract_token_from_headers`），不转发 `X-Intellect-*` 归因头；而 REST 侧服务 token 路径要求成员头（无头 401）。
**影响**：以服务 key 认证的 MCP 客户端（含 openkg-webui 向 CLI 后端下发的 `.mcp.json` 注入）无法完成成员归因。
**验证证据**：REST 直连带成员头 200 / 不带头 401；MCP 端点 initialize 200（Bearer 有效）但 tools/list 恒 401（即使客户端带成员头）。
**修复方向**：(a) host 模式透传客户端 `X-Intellect-User/Team/Project` 头（最小改动）；(b) 启动参数固定成员；(c) 改用 `imt_` 令牌（依赖 TEAM 验证配置，本部署未启用）。
**当前状态**：已记录待提 issue（Gitee `wustbd/intellect-rag-app` 的 issue API 对当前 token 返 404，GitHub org 无该仓库镜像）；A5 的**单元与端点级验收已完成**（`.mcp.json` 合并写入、权限放行、401 门 + initialize 200），工具调用级验收待该缺陷修复。

## 5. 验收速查

```bash
# 状态门（enabled + identity_ok）
curl http://localhost:3300/api/knowledge-center
# 建库 / 上传 / 检索
curl -X POST http://localhost:3300/api/knowledge-center/datasets -H 'Content-Type: application/json' \
  -d '{"name":"demo","permission":"me"}'
curl -X POST http://localhost:3300/api/knowledge-center/datasets/<id>/documents \
  -F 'type=local' -F 'file=@doc.txt'
curl -X POST http://localhost:3300/api/knowledge-center/datasets/<id>/search \
  -H 'Content-Type: application/json' -d '{"question":"...","similarity_threshold":0.1}'
```

聊天接线（Phase 1b）：openkg-webui 的 runs 请求自动携带 `rag` 块（知识中心启用时）；网关日志可见 `POST /api/v1/retrieval → 200`。

**已知遗留（Phase 1b，intellect-team 侧）**：网关检索调用使用**其自身服务成员**的身份（rag-app 侧收到的 X-Intellect-User 是网关配置的成员，而非发起 run 的用户）——runs 请求携带的 `X-Intellect-User` 归因未传导到检索调用。后果：用户会话可能召回服务成员可见但其自身不可见的库内容（越权暴露方向），且 `rag.knowledge_base_ids` 对服务成员不可见的库静默失效（rag-app 返回 `denied_dataset_ids`，行为可预期但非用户意图）。rag-app 侧访问控制本身验证无泄漏（`denied_dataset_ids` 机制精确）。修复方向：网关 run 路径以 member_context 构建 RAG provider 的 identity。
