# A-S0 Schema wire 契约拦包实测归档（方案 A 前置 gate）

> 状态：**通过（U1–U6 全 PASS）**；探针项目 m2ReviewProj 除下述 5 个历史孤儿外净零残留。
> 日期：2026-09-22；方法：M3.5 同款（拦截 knext `SchemaApi.schema_alter_schema_post_with_http_info`，
> 用 `sanitize_for_serialization` 抓真实发往 `/public/v1/schema/alterSchema` 的 wire，重放后回读确认）。
> 探针脚本：`scripts/kag_a0/a0_schema_wire_probe.py`；结果：`results/a0_schema_wire_probe.json`。

## 0. 拦包方法
- 拦截点：`SchemaApi.schema_alter_schema_post_with_http_info`，`body = api_client.sanitize_for_serialization(schema_alter_request)`。
- 单进程新开 `SchemaSession`（绕过 knext 进程内 schema 缓存），回读用直连 REST（server 真值），规避 M3.5 的"进程内缓存假阳性"。
- wire 键为 **camelCase**：`schemaDraft.alterSpgTypes[]`。
- 环境：OpenSPG server :8887；探针项目 **m2ReviewProj（id=4）**；knext 源码 `~/project/KAG`（py3.11 venv `/tmp/kag-a0-venv`）。

## 1. 命名规则（server 强校验，OPENKG-WebUI 后端必须内建）
| 对象 | 规则 | 实测反例 |
|---|---|---|
| SPG 类型名 | `^[A-Z][a-zA-Z0-9]*`（大写开头、无点无下划线），且 wire 内为**全名 `ns.Type`** | `a0type` 报 "not match ^[A-Z]"；裸名 `A0Type` 报 "not match project namespace" |
| parent 类型 | 全名 `ns.Type` | 裸名 `Person` 报 "there is no spg type with name=Person" |
| 属性/关系名 | `^[a-z][0-9a-zA-Z]*`（小写开头、无下划线） | `As0_prop` 报 "pattern not match" |
| 属性 nameZh | **必填（create 时非空）** | 缺省报 "property/relation: null nameZh can not be null" |

结论：schema_draft.py 的 `new_property`/`new_spg_type` 构造器应断言这些命名规则，注入错误可读化提示。

## 2. wire 权威样本（可直接复用于后端构造器）
### 2.1 属性 CREATE（元素级 `alterOperation=CREATE`）
```json
{
  "basicInfo": {
    "name": {"identityType": "PREDICATE", "name": "a0prop…", "@type": "PREDICATE"},
    "nameZh": "探针属性", "desc": "A-S0 property"
  },
  "subjectTypeRef": {"basicInfo": {"name": {"identityType": "SPG_TYPE", "@type": "SPG_TYPE"}}},
  "objectTypeRef": {
    "basicInfo": {"name": {"identityType": "SPG_TYPE", "nameEn": "Text", "@type": "SPG_TYPE"}},
    "spgTypeEnum": "BASIC_TYPE"
  },
  "advancedConfig": {
    "constraint": {"constraintItems": [{"constraintTypeEnum": "NOT_NULL", "@type": "NOT_NULL"}]},
    "subProperties": [], "semantics": []
  },
  "alterOperation": "CREATE"
}
```

### 2.2 类型 CREATE（类型级 `alterOperation=CREATE` + `@type=ENTITY_TYPE`）
```json
{
  "basicInfo": {
    "name": {"identityType": "SPG_TYPE", "namespace": "m2ReviewProj", "nameEn": "A0Type…", "@type": "SPG_TYPE"},
    "nameZh": "探针实体类型", "desc": "A-S0 probe entity type"
  },
  "parentTypeInfo": {
    "parentTypeIdentifier": {"identityType": "SPG_TYPE", "namespace": "m2ReviewProj", "nameEn": "Person", "@type": "SPG_TYPE"},
    "inheritPath": []
  },
  "spgTypeEnum": "ENTITY_TYPE",
  "properties": [/* 自带属性，各属性 nameZh 非空 */],
  "relations": [],
  "alterOperation": "CREATE",
  "@type": "ENTITY_TYPE"
}
```

### 2.3 属性 UPDATE（**无元素级标记**，靠类型 UPDATE 整型覆写）
- 实测：对"改 nameZh/desc 的已有属性"，knext **不给属性元素打 `alterOperation`**（=None），只把类型标记 `UPDATE` 并将更新后的属性值一并提交。
- 结论：**属性"改"= 类型 UPDATE 覆写**（改后的属性放回 properties 列表，类型带 UPDATE）。这印证 schema_draft.py 整型覆写语义；前端无需对属性声明 UPDATE 意图，只需回传更新后的属性值。
- （注：schema_draft 现 `_slim_item` 定位在属性元素，本结论下"改"天然被 UPDATE 覆盖。）

### 2.4 属性 DELETE（元素级 `alterOperation=DELETE`）
- 属性元素带 `alterOperation="DELETE"`，其余同 2.1 骨架；服务端即删。
- 结论（M3.5 重申）：**缺条目不等于删除**，必须显式元素级 DELETE。

### 2.5 类型 DELETE（类型级 `alterOperation=DELETE`）
- 类型元素带 `alterOperation="DELETE"`，且 **必须保留非空 `parentTypeInfo.parentTypeIdentifier`**（删除时 server 仍校验 parent 非空）。

## 3. DROP 级联约束发现（重要）
- **直接 drop 父类型（存在子类型）→ server 200"允许"，但子类型被孤立为 `parent=null` 的孤儿**，且该孤儿**后续 delete/update 均被拒**（`parent type can not be null`）→ **API 不可还原的永久残留**。
- 正确顺序：**先删子类型，再删父类型**（两者皆成功、净零；U6 验证）。
- 对孤儿类型，尝试"补 parent 再 delete"：alform 200 但**未真正持久化删除**（仍残留）。
- **OPENKG-WebUI 落地**：删除类型功能必须先校验/级联删除子类型，或在前端阻止删除"有子类型的类型"；不要给"孤儿类型删除"UI。

## 4. 概念树端点探测（A3 可行性）
- `GET /public/v1/concept/getConceptTree`、`getConceptDetail` → **404（不存在）**。
- 可用：`queryConcept`（某类型下全部概念及语义）、`getReasoningConcept`、`defineDynamicTaxonomy`、`removeDynamicTaxonomy`、`defineLogicalCausation`、`removeLogicalCausation`（C2 已实现）。
- 结论：**完整概念树浏览无公开端点**，A3 降级为"按概念类型用 queryConcept 聚合成概念列表/树视图"，不做独立 tree 端点浏览。

## 5. 已知残留（探针项目 m2ReviewProj）
- 5 个 `A0Child*` 空类型（早期 U6 级联删除牺牲品，见 §3）：`A0Child179005919428890254` 等，无属性、无实例数据，API 不可删；不影响功能。如需彻底清除需直连 server 存储。
- 后续 U1–U6 运行自身净零（每次 `A0*`/`a0*` 命名 + 幂等清理）。

## 6. 落地指引（schema_draft.py + kag.py /schema/alter 扩展）
- `new_property`：按 §2.1 构造；`new_spg_type`（EntityType CREATE）按 §2.2，`delete_spg_type` 按 §2.5（保留 parent 非空）；属性 UPDATE 走整型覆写（§2.3）。
- 命名校验前置（§1）：类型/属性名规则 + 属性 nameZh 必填。
- 类型删除做子类型预检（§3）。
- 概念树：不做独立 tree 端点（§4），聚合浏览。