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
