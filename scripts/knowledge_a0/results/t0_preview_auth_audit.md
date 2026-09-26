# T0 预览/缩略图链路鉴权审计（静态证据链）

- 日期：2026-09-26
- 结论：**preview 放行**（上游三层 per-user 强制，证据如下）；**thumbnails 不接**（上游无
  user 维度过滤，P0 预览不依赖缩略图）
- 方式：静态审计运行中上游（:9380，editable install 即运行代码）+ 本仓代理链路核对；
  双用户 live deny 探针留作部署验收可选项（非阻塞）

## 一、上游 preview 端点：per-user 可见性强制 ✅

证据链（运行代码 = 两个 editable install 的命名空间合并）：

1. **端点检查**：`GET /documents/{doc_id}/preview` 调用
   `DocumentService.accessible(doc_id, current_user.id)`，失败返回与"文档不存在"同型的
   响应（防枚举）。
   `intellect-rag-app/api/apps/restful_apis/document_api.py:2106-2114`
2. **accessible 语义**：`DocumentService.accessible` → `KnowledgebaseService.accessible(kb_id, user_id)`；
   多租户模式走 `kb_accessible` → `can_access_resource`——注释明确其为
   "owner/private/tenant/team/project visibility 的单一权威"。
   `intellect-rag/api/db/services/document_service.py:764-770`、
   `intellect-rag/api/db/services/knowledgebase_service.py:580-615`、
   `intellect-rag/api/db/access_control.py:487-512`
3. **委托身份成立**：openkg 引擎每请求携带 `Authorization: Bearer <服务key>` +
   `X-Intellect-User/Team/Project`（`engines/intellect_rag.py:258-261`）。上游 service-key
   分支**按头构造 g.user**（`ensure_team_user(member_id)` 幂等建户 + 按 `id=member_id`
   查询），即 `current_user.id` == 被委托用户；
   头与已认证主体不一致时 fail-closed 钳制。
   `intellect-rag-app/api/apps/__init__.py:282-313`、`api/utils/api_utils.py:246-282`

## 二、上游 thumbnails / images：无 user 维度过滤 ⚠️（不接）

- `GET /thumbnails`（`document_api.py:1371-1420`）：仅 `login_required`；
  `DocumentService.get_thumbnails(doc_ids)` 只按 doc_id 集合查
  （`intellect-rag/api/db/services/document_service.py:853-855`），无 user 过滤，
  响应含 `kb_id` 与 `thumbnail` 键。
- `GET /documents/images/{image_id}`（`document_api.py:1884-1918`）：仅 `login_required`，
  无 accessible 检查；image_id = `{kb_id}-{key}` 复合键。
- 风险评估：利用需先持有合法 doc_id（UUID 枚举成本高），属低危信息面缺口，
  但**不满足与 preview 同等强制**。处置：本仓首版预览不接 thumbnails；
  若后续列表要缩略图，须先在代理侧补 doc→dataset→visibility 校验，并向上游同步修复建议。

## 三、对 P0-T2 的修订

T2 预览抽屉**只接 `GET /api/knowledge-center/documents/{doc_id}/preview`**，
不接 `/thumbnails`（原方案 §四 T2 写了 thumbnails，据本审计移除）。
