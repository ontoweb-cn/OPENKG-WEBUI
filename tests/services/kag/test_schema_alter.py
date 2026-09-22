# -*- coding: utf-8 -*-
"""A-S1：/schema/alter 意图组装与校验测试（属性/类型增删、命名校验、
inherited 禁删、子类型拒删、向后兼容）。

直接调用 ``alter_project_schema`` 路由函数（不面 HTTP 样板），monkeypatch
访问/同源门禁为 no-op、``_client()`` 返回捕获型 fake，专注断言 wire 组装与
服务端校验（A-S0 实测契约）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openkg_webui.api.routers.kag import (
    KagPropertyAdd,
    KagSchemaEditRequest,
    KagTypeAdd,
    alter_project_schema,
)

# 读模型 fixture（basicInfo.name 带 namespace；querySchema 的 nameEn 匹配）
PERSON = {
    "@type": "ENTITY_TYPE",
    "spgTypeEnum": "ENTITY_TYPE",
    "basicInfo": {
        "name": {"@type": "SPG_TYPE", "namespace": "m2ReviewProj", "nameEn": "Person", "identityType": "SPG_TYPE"},
        "nameZh": "人物",
        "desc": "",
    },
    "parentTypeInfo": {
        "parentTypeIdentifier": {"@type": "SPG_TYPE", "namespace": "m2ReviewProj", "nameEn": "Thing", "identityType": "SPG_TYPE"}
    },
    "properties": [
        {
            "basicInfo": {"name": {"@type": "PREDICATE", "name": "id", "identityType": "PREDICATE"}, "nameZh": "标识"},
            "objectTypeRef": {"basicInfo": {"name": {"@type": "SPG_TYPE", "nameEn": "Text", "identityType": "SPG_TYPE"}}, "spgTypeEnum": "BASIC_TYPE"},
            "advancedConfig": {"indexType": None, "subProperties": [], "semantics": []},
            "inherited": True,
        }
    ],
    "relations": [],
}
ORGANIZATION = {
    "@type": "ENTITY_TYPE", "spgTypeEnum": "ENTITY_TYPE",
    "basicInfo": {"name": {"@type": "SPG_TYPE", "namespace": "m2ReviewProj", "nameEn": "Organization", "identityType": "SPG_TYPE"}, "nameZh": "组织机构", "desc": ""},
    "parentTypeInfo": {"parentTypeIdentifier": {"@type": "SPG_TYPE", "namespace": "m2ReviewProj", "nameEn": "Thing", "identityType": "SPG_TYPE"}},
    "properties": [], "relations": [],
}
PRODUCT = {
    "@type": "ENTITY_TYPE", "spgTypeEnum": "ENTITY_TYPE",
    "basicInfo": {"name": {"@type": "SPG_TYPE", "namespace": "m2ReviewProj", "nameEn": "Product", "identityType": "SPG_TYPE"}, "nameZh": "产品", "desc": ""},
    "parentTypeInfo": {"parentTypeIdentifier": {"@type": "SPG_TYPE", "namespace": "m2ReviewProj", "nameEn": "Person", "identityType": "SPG_TYPE"}},
    "properties": [], "relations": [],
}
TEXT = {
    "basicInfo": {"name": {"@type": "SPG_TYPE", "nameEn": "Text", "identityType": "SPG_TYPE"}},
    "spgTypeEnum": "BASIC_TYPE",
}


class FakeClient:
    def __init__(self, types):
        self.types = types
        self.captured: dict = {}

    async def query_schema(self, project_id):
        return {"spgTypes": self.types}

    async def alter_schema(self, project_id, drafts):
        self.captured["drafts"] = drafts
        return {"ok": True}


async def _call(fake, payload):
    import openkg_webui.api.routers.kag as kag_mod

    mp = pytest.MonkeyPatch()
    mp.setattr(kag_mod, "_require_project_access", lambda pid: None)
    mp.setattr(kag_mod, "_require_same_origin", lambda r: None)
    mp.setattr(kag_mod, "_client", lambda: fake)
    try:
        return await alter_project_schema(SimpleNamespace(), "3", payload)
    finally:
        mp.undo()


def _person_payload(**extra) -> KagSchemaEditRequest:
    base = {"spg_type": PERSON}
    base.update(extra)
    return KagSchemaEditRequest(**base)


# ---- 门禁与空意图 ----


@pytest.mark.asyncio
async def test_pure_update_label_passes_as_update() -> None:
    """A-S2：无 add/delete 意图 = 纯 UPDATE 整型覆写（改 nameZh/desc 用），幂等提交。"""
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    await _call(fake, _person_payload())  # spg_type 原样，无任何意图
    drafts = fake.captured["drafts"]
    assert len(drafts) == 1
    assert drafts[0]["alterOperation"] == "UPDATE"


@pytest.mark.asyncio
async def test_missing_namespace_rejected() -> None:
    bad = {**PERSON}
    bad["basicInfo"] = {**PERSON["basicInfo"], "name": {"@type": "SPG_TYPE", "nameEn": "Person", "identityType": "SPG_TYPE"}}
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    with pytest.raises(HTTPException) as ei:
        await _call(fake, KagSchemaEditRequest(spg_type=bad, add_types=[KagTypeAdd(name="Product", parent_name="Person")]))
    assert ei.value.status_code == 400
    assert "namespace" in ei.value.detail


# ---- 属性：新增 / 删除 ----


@pytest.mark.asyncio
async def test_add_property_wire_create() -> None:
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    await _call(fake, _person_payload(add_properties=[KagPropertyAdd(name="nickname", object_type_name="Text", name_zh="昵称")]))
    drafts = fake.captured["drafts"]
    assert len(drafts) == 1
    prop = next(p for p in drafts[0]["properties"] if p["basicInfo"]["name"]["name"] == "nickname")
    assert prop["alterOperation"] == "CREATE"
    assert prop["objectTypeRef"]["basicInfo"]["name"]["nameEn"] == "Text"
    assert prop["objectTypeRef"]["spgTypeEnum"] == "BASIC_TYPE"
    assert prop["basicInfo"]["nameZh"] == "昵称"
    assert prop["advancedConfig"]["constraint"]["constraintItems"] == []


@pytest.mark.asyncio
async def test_add_property_invalid_name_rejected() -> None:
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    with pytest.raises(HTTPException) as ei:
        await _call(fake, _person_payload(add_properties=[KagPropertyAdd(name="BadName", object_type_name="Text", name_zh="x")]))
    assert ei.value.status_code == 400
    assert "must match" in ei.value.detail  # A-S0 命名规则拒绝


@pytest.mark.asyncio
async def test_add_property_missing_namezh_rejected() -> None:
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    with pytest.raises(HTTPException) as ei:
        await _call(fake, _person_payload(add_properties=[KagPropertyAdd(name="nickname", object_type_name="Text", name_zh="")]))
    assert ei.value.status_code == 400
    assert "nameZh" in ei.value.detail


@pytest.mark.asyncio
async def test_delete_inherited_property_rejected() -> None:
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    with pytest.raises(HTTPException) as ei:
        await _call(fake, _person_payload(delete_properties=["id"]))  # id 是 inherited
    assert ei.value.status_code == 400
    assert "inherited" in ei.value.detail


@pytest.mark.asyncio
async def test_delete_unknown_property_rejected() -> None:
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    with pytest.raises(HTTPException) as ei:
        await _call(fake, _person_payload(delete_properties=["ghost"]))
    assert ei.value.status_code == 400
    assert "properties not found" in ei.value.detail


# ---- 类型：新增 / 删除 ----


@pytest.mark.asyncio
async def test_add_type_wire_create_full_name() -> None:
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    await _call(fake, _person_payload(add_types=[KagTypeAdd(name="Device", name_zh="设备", desc="", parent_name="Person")]))
    drafts = fake.captured["drafts"]
    assert len(drafts) == 2
    new = drafts[1]
    assert new["alterOperation"] == "CREATE"
    assert new["@type"] == "ENTITY_TYPE"
    assert new["spgTypeEnum"] == "ENTITY_TYPE"
    assert new["basicInfo"]["name"]["nameEn"] == "Device"
    assert new["basicInfo"]["name"]["namespace"] == "m2ReviewProj"
    assert new["parentTypeInfo"]["parentTypeIdentifier"]["nameEn"] == "Person"


@pytest.mark.asyncio
async def test_add_type_non_entity_rejected() -> None:
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    with pytest.raises(HTTPException) as ei:
        await _call(fake, _person_payload(add_types=[KagTypeAdd(name="Product", parent_name="Person", spg_type="INDEX_TYPE")]))
    assert ei.value.status_code == 400
    assert "ENTITY_TYPE" in ei.value.detail


@pytest.mark.asyncio
async def test_add_type_invalid_name_rejected() -> None:
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    with pytest.raises(HTTPException) as ei:
        await _call(fake, _person_payload(add_types=[KagTypeAdd(name="badName", parent_name="Person")]))
    assert ei.value.status_code == 400
    assert "must match" in ei.value.detail  # A-S0 命名规则拒绝


@pytest.mark.asyncio
async def test_delete_type_with_subtype_rejected() -> None:
    # Person 有子类型 Product → 拒删
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    with pytest.raises(HTTPException) as ei:
        await _call(fake, _person_payload(delete_types=["Person"]))
    assert ei.value.status_code == 400
    assert "subtype" in ei.value.detail


@pytest.mark.asyncio
async def test_delete_leaf_type_wire_delete() -> None:
    node = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    await _call(node, _person_payload(delete_types=["Product"]))
    drafts = node.captured["drafts"]
    assert len(drafts) == 2
    assert drafts[1]["alterOperation"] == "DELETE"
    assert drafts[1]["basicInfo"]["name"]["nameEn"] == "Product"


@pytest.mark.asyncio
async def test_delete_unknown_type_rejected() -> None:
    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    with pytest.raises(HTTPException) as ei:
        await _call(fake, _person_payload(delete_types=["Ghost"]))
    assert ei.value.status_code == 400
    assert "type not found" in ei.value.detail


# ---- 向后兼容：旧客户端新增字段省略仍工作 ----


@pytest.mark.asyncio
async def test_legacy_relation_intent_still_works() -> None:
    from openkg_webui.api.routers.kag import KagSchemaRelationAdd

    fake = FakeClient([PERSON, ORGANIZATION, TEXT, PRODUCT])
    await _call(fake, _person_payload(add_relations=[KagSchemaRelationAdd(name="mentorOf", name_zh="指导", desc="", object_type_name="Organization")]))
    drafts = fake.captured["drafts"]
    rel = drafts[0]["relations"][0]
    assert rel["alterOperation"] == "CREATE"
    assert rel["basicInfo"]["name"]["name"] == "mentorOf"
    assert rel["objectTypeRef"]["basicInfo"]["name"]["nameEn"] == "Organization"