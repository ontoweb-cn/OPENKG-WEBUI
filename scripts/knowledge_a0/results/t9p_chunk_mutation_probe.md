# T9-P chunk 变更类端点探针（联调测试库，自建自删）

- 日期：2026-09-26；对象：联调测试库(1) / 32f65cf2…b9b 文档；探针 chunk 已删除（list 复核 absent）
- 上游 base：`http://127.0.0.1:9380/api/v1`，service key + `X-Intellect-User: local-admin`

## 实测结论

| 操作 | 请求 | 结果 |
| --- | --- | --- |
| POST 新建 | `POST /datasets/{ds}/documents/{doc}/chunks` body `{"content": "…"}` | `code 0`；返回新 chunk（键：id/content/important_keywords/questions/dataset_id/document_id/create_time/create_timestamp） |
| PATCH 更新 | `PATCH …/chunks/{cid}` body `{"content":"…","available":false,"important_keywords":["probe"]}` | `code 0`；`available`(bool) → 存储为 `available_int`(0/1) |
| GET 单个 | `GET …/chunks/{cid}` | `code 0` 但**不含 content**（形状受限）——回读验证须走 LIST |
| DELETE 单个 | `DELETE …/chunks/{cid}` | **405 Method Not Allowed** |
| DELETE 批量 | `DELETE …/chunks` body `{"chunk_ids":[cid]}` | `code 0`；list 复核已删除 |

## 对实现的约束

1. 删除走**集合端点 + chunk_ids body**，不做 item DELETE；
2. update 的 available 用 bool，engine 归一读侧以 `available_int` 为权威
   （list 载荷中 `available` 键存在但可为 null）；
3. 编辑后不依赖单 GET 回读（无 content）——前端以 PATCH code 0 + 列表刷新为准。
