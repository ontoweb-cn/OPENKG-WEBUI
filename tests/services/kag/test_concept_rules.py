# -*- coding: utf-8 -*-
"""C2 概念建模测试：OpenSPG 概念 client（MockTransport）、归一 helper、
路由（浏览归一 / belong_to_ready 门禁 / 增删 / 权限 / 降级）。

契约基线：C2 实测归档（docs/plans/2026-09-19-kag-ui-observability-plan.md
§5.2）——/public/v1/concept/* 响应均为裸对象/裸 bool（无 execute2 信封）；
getReasoningConcept 按 object 概念类型过滤；defineDynamicTaxonomy 前置依赖
schema 中存在 belongTo 属性（否则 server 500 NPE）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from openkg_webui.services.kag.openspg_client import OpenSPGClient, OpenSPGError

# C2 实测 wire 形态样本（@type/identityType 为 Jackson 多态标记）
TRIPLE_LEAD_TO = {
    "@type": "TripleSemantic",
    "subjectTypeIdentifier": {"@type": "SPG_TYPE", "namespace": "m0ProbeLive", "nameEn": "Topic", "identityType": "SPG_TYPE"},
    "subjectIdentifier": {"@type": "CONCEPT", "id": "1", "name": "1", "identityType": "CONCEPT"},
    "predicateIdentifier": {"@type": "PREDICATE", "name": "leadTo", "identityType": "PREDICATE"},
    "objectTypeIdentifier": {"@type": "SPG_TYPE", "namespace": "m0ProbeLive", "nameEn": "Topic", "identityType": "SPG_TYPE"},
    "objectIdentifier": {"@type": "CONCEPT", "id": "2", "name": "2", "identityType": "CONCEPT"},
    "logicalRule": {"code": {"code": "RULE_1"}, "version": 1, "status": "PROD", "content": "Define (s:...){...}"},
    "ontologyEnum": "CONCEPT",
}
DYNAMIC_TAXONOMY = {
    "@type": "DynamicTaxonomySemantic",
    "predicateIdentifier": {"@type": "PREDICATE", "name": "belongTo", "identityType": "PREDICATE"},
    "conceptTypeIdentifier": {"@type": "SPG_TYPE", "namespace": "m0ProbeLive", "nameEn": "Topic", "identityType": "SPG_TYPE"},
    "conceptIdentifier": {"@type": "CONCEPT", "id": "1", "name": "1", "identityType": "CONCEPT"},
    "logicalRule": {"code": {"code": "RULE_2"}, "status": "PROD", "content": "Define (s:...)-[p:belongTo]->(...){...}"},
    "ontologyEnum": "CONCEPT",
}


# ---------------------------------------------------------------------------
# 概念 client（httpx.MockTransport，无网络）
# ---------------------------------------------------------------------------


def _mock_client(handler) -> OpenSPGClient:
    return OpenSPGClient("http://spg.test", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_reasoning_concepts_passthrough_list() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[TRIPLE_LEAD_TO])

    rows = await _mock_client(handler).get_reasoning_concepts("m0ProbeLive.Topic")
    assert rows == [TRIPLE_LEAD_TO]
    assert seen == ["http://spg.test/public/v1/concept/getReasoningConcept?name=m0ProbeLive.Topic"]


@pytest.mark.asyncio
async def test_get_reasoning_concepts_degraded_empty() -> None:
    client = _mock_client(lambda request: httpx.Response(200, text="boom"))
    assert await client.get_reasoning_concepts("m0ProbeLive.Topic") == []


@pytest.mark.asyncio
async def test_get_concept_detail_params_and_degrade() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"concepts": []})

    client = _mock_client(handler)
    assert await client.get_concept_detail("m0ProbeLive.Topic", "1") == {"concepts": []}
    assert captured["url"] == (
        "http://spg.test/public/v1/concept/queryConcept?conceptTypeName=m0ProbeLive.Topic&conceptName=1"
    )
    # conceptName 可省（全部概念）
    await client.get_concept_detail("m0ProbeLive.Topic")
    assert "conceptName" not in captured["url"]
    # 空体降级
    empty = _mock_client(lambda request: httpx.Response(200, text="boom"))
    assert await empty.get_concept_detail("m0ProbeLive.Topic") == {}


@pytest.mark.asyncio
async def test_query_concept_level_instance_params_and_degrade() -> None:
    """I3 client：GET /conceptInstance/level 参数（conceptType 必有，
    rootConceptInstance/projectId 可省）与空体降级。"""
    captured: list[str] = []
    payload = {
        "conceptType": "m0ProbeLive.Topic",
        "rootConceptInstance": "ROOT",
        "children": [{"id": "A", "properties": {"nameZh": "甲", "name": "A"}}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, json=payload)

    client = _mock_client(handler)
    assert await client.query_concept_level_instance("m0ProbeLive.Topic") == payload
    assert captured[0] == "http://spg.test/public/v1/conceptInstance/level?conceptType=m0ProbeLive.Topic"
    # root 与 projectId 可选追加
    await client.query_concept_level_instance("m0ProbeLive.Topic", "abc", 7)
    assert "rootConceptInstance=abc" in captured[1]
    assert "projectId=7" in captured[1]
    # 空体/非 dict 降级为 {children:[]}
    empty = _mock_client(lambda request: httpx.Response(200, text="boom"))
    assert await empty.query_concept_level_instance("m0ProbeLive.Topic") == {"children": []}


@pytest.mark.asyncio
async def test_concept_write_methods_send_expected_bodies() -> None:
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({"url": str(request.url), "body": json.loads(request.content or b"{}")})
        return httpx.Response(200, json=True)

    client = _mock_client(handler)
    assert await client.define_dynamic_taxonomy("m0ProbeLive.Topic", "1", "dsl-x") is True
    assert await client.remove_dynamic_taxonomy("m0ProbeLive.Topic", "1") is True
    assert await client.define_logical_causation(
        subject_concept_type_name="m0ProbeLive.Topic",
        subject_concept_name="1",
        predicate_name="leadTo",
        object_concept_type_name="m0ProbeLive.Topic",
        object_concept_name="2",
        dsl="dsl-y",
    ) is True
    assert await client.remove_logical_causation(
        subject_concept_type_name="m0ProbeLive.Topic",
        subject_concept_name="1",
        predicate_name="leadTo",
        object_concept_type_name="m0ProbeLive.Topic",
        object_concept_name="2",
    ) is True

    assert captured[0]["url"].endswith("/concept/defineDynamicTaxonomy")
    assert captured[0]["body"] == {"conceptTypeName": "m0ProbeLive.Topic", "conceptName": "1", "dsl": "dsl-x"}
    # removeDynamicTaxonomy 字段名不同（objectConcept*，C2 实测）
    assert captured[1]["url"].endswith("/concept/removeDynamicTaxonomy")
    assert captured[1]["body"] == {"objectConceptTypeName": "m0ProbeLive.Topic", "objectConceptName": "1"}
    assert captured[2]["url"].endswith("/concept/defineLogicalCausation")
    assert captured[2]["body"]["semanticType"] == "REASONING_CONCEPT"
    assert captured[3]["url"].endswith("/concept/removeLogicalCausation")
    assert "dsl" not in captured[3]["body"]


# ---------------------------------------------------------------------------
# 归一 helper
# ---------------------------------------------------------------------------


def test_parse_triple_semantic_normalizes_wire() -> None:
    from openkg_webui.api.routers.kag import _parse_triple_semantic, _spg_type_name

    assert _spg_type_name({"namespace": "m0ProbeLive", "nameEn": "Topic"}) == "m0ProbeLive.Topic"
    assert _spg_type_name({"nameEn": "Person"}) == "Person"
    assert _spg_type_name(None) == ""

    rule = _parse_triple_semantic(TRIPLE_LEAD_TO)
    assert rule == {
        "kind": "logical",
        "subject_type": "m0ProbeLive.Topic",
        "subject_name": "1",
        "predicate": "leadTo",
        "object_type": "m0ProbeLive.Topic",
        "object_name": "2",
        "dsl": "Define (s:...){...}",
    }
    # 缺 subject/object 类型 → None（不渲染畸形行）
    assert _parse_triple_semantic({"subjectTypeIdentifier": {"namespace": "x", "nameEn": "A"}}) is None
    assert _parse_triple_semantic("nope") is None


def test_parse_dynamic_taxonomy_normalizes_wire() -> None:
    from openkg_webui.api.routers.kag import _parse_dynamic_taxonomy

    assert _parse_dynamic_taxonomy(DYNAMIC_TAXONOMY) == {
        "kind": "taxonomy",
        "concept_name": "1",
        "dsl": "Define (s:...)-[p:belongTo]->(...){...}",
    }
    assert _parse_dynamic_taxonomy({"conceptIdentifier": {}}) is None
    assert _parse_dynamic_taxonomy(None) is None


def test_belong_to_ready_scans_schema_properties() -> None:
    from openkg_webui.api.routers.kag import _belong_to_ready

    schema = {
        "spgTypes": [
            {
                "properties": [
                    {
                        "basicInfo": {"name": {"name": "belongTo"}},
                        "objectTypeRef": {
                            "basicInfo": {"name": {"namespace": "m0ProbeLive", "nameEn": "Topic"}}
                        },
                    }
                ]
            },
            {"properties": []},
        ]
    }
    assert _belong_to_ready(schema, "m0ProbeLive.Topic") is True
    assert _belong_to_ready(schema, "m0ProbeLive.Other") is False
    assert _belong_to_ready({}, "m0ProbeLive.Topic") is False
    assert _belong_to_ready({"spgTypes": "nope"}, "m0ProbeLive.Topic") is False


# ---------------------------------------------------------------------------
# 路由级（FastAPI TestClient；_client 注入 FakeConceptOpenSPG）
# ---------------------------------------------------------------------------


class FakeConceptOpenSPG:
    """_client() 注入替身：方法级 error 注入 + 提交记录捕获。"""

    def __init__(
        self,
        reasoning=None,
        concepts=None,
        schema=None,
        levels=None,
        errors=(),
    ):
        self.reasoning = reasoning or []
        self.concepts = concepts or []
        self.schema = schema
        self.levels = levels or {}
        self.errors = set(errors)
        self.submitted = []

    async def get_reasoning_concepts(self, concept_type_name):
        if "get_reasoning_concepts" in self.errors:
            raise OpenSPGError("boom")
        return self.reasoning

    async def query_concept_level_instance(self, concept_type_name, root_concept_instance="", project_id=""):
        if "query_concept_level_instance" in self.errors:
            raise OpenSPGError("boom")
        return self.levels.get(root_concept_instance or "", {"children": []})

    async def get_concept_detail(self, concept_type_name, concept_name=""):
        if "get_concept_detail" in self.errors:
            raise OpenSPGError("boom")
        return {"concepts": self.concepts}

    async def query_schema(self, project_id):
        if "query_schema" in self.errors:
            raise OpenSPGError("boom")
        return self.schema

    async def define_dynamic_taxonomy(self, concept_type_name, concept_name, dsl):
        self.submitted.append(("define_taxonomy", concept_type_name, concept_name, dsl))
        return True

    async def remove_dynamic_taxonomy(self, concept_type_name, concept_name):
        self.submitted.append(("remove_taxonomy", concept_type_name, concept_name))
        return True

    async def define_logical_causation(self, **kwargs):
        self.submitted.append(("define_logical", kwargs))
        return True

    async def remove_logical_causation(self, **kwargs):
        self.submitted.append(("remove_logical", kwargs))
        return True


def _patch_router_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import openkg_webui.multi_user.paths as paths_mod
    import openkg_webui.services.kag.task_store as store_mod

    monkeypatch.setattr(paths_mod, "SYSTEM_ROOT", tmp_path)
    monkeypatch.setattr(store_mod, "_store_path", lambda: tmp_path / "kag_tasks.json")


def _router_client(monkeypatch: pytest.MonkeyPatch, fake: FakeConceptOpenSPG, user: SimpleNamespace):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openkg_webui.api.routers.kag import router

    monkeypatch.setattr("openkg_webui.api.routers.kag._client", lambda: fake)
    monkeypatch.setattr("openkg_webui.api.routers.kag.kag_enabled", lambda: True)
    monkeypatch.setattr("openkg_webui.api.routers.kag._current_user", lambda: user)

    app = FastAPI()
    app.include_router(router, prefix="/api/kag")
    return TestClient(app)


_ADMIN = SimpleNamespace(user_id="admin", role="admin", is_admin=True)
_OUTSIDER = SimpleNamespace(user_id="eve", role="user", is_admin=False)

SCHEMA_WITH_BELONG = {
    "spgTypes": [
        {
            "properties": [
                {
                    "basicInfo": {"name": {"name": "belongTo"}},
                    "objectTypeRef": {
                        "basicInfo": {"name": {"namespace": "m0ProbeLive", "nameEn": "Topic"}}
                    },
                }
            ]
        }
    ]
}


def test_get_concept_rules_normalizes_both_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    fake = FakeConceptOpenSPG(
        reasoning=[TRIPLE_LEAD_TO],
        concepts=[{"name": {"id": "1"}, "semantics": [DYNAMIC_TAXONOMY]}],
        schema=SCHEMA_WITH_BELONG,
    )
    client = _router_client(monkeypatch, fake, _ADMIN)
    resp = client.get("/api/kag/projects/3/concept/rules", params={"type_name": "m0ProbeLive.Topic"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type_name"] == "m0ProbeLive.Topic"
    assert body["belong_to_ready"] is True
    assert body["reasoning"][0]["predicate"] == "leadTo"
    assert body["reasoning"][0]["object_name"] == "2"
    assert body["taxonomy"][0]["concept_name"] == "1"
    # A-S3：概念实例名（_concept_name 取 name||id，去重保序）
    assert body["concepts"] == ["1"]
    # 非 DynamicTaxonomySemantic 语义行不混入 taxonomy
    fake2 = FakeConceptOpenSPG(
        reasoning=[], concepts=[{"name": {"id": "1"}, "semantics": [TRIPLE_LEAD_TO]}], schema={}
    )
    client2 = _router_client(monkeypatch, fake2, _ADMIN)
    body2 = client2.get(
        "/api/kag/projects/3/concept/rules", params={"type_name": "m0ProbeLive.Topic"}
    ).json()
    assert body2["taxonomy"] == []


def test_get_concept_rules_membership_and_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    # 非成员 → 403
    outsider = _router_client(monkeypatch, FakeConceptOpenSPG(), _OUTSIDER)
    assert (
        outsider.get("/api/kag/projects/3/concept/rules", params={"type_name": "x"}).status_code == 403
    )
    # type_name 必填 → 400
    client = _router_client(monkeypatch, FakeConceptOpenSPG(), _ADMIN)
    assert client.get("/api/kag/projects/3/concept/rules").status_code == 400


def test_get_concept_rules_degrades_per_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上游失败时 reasoning/taxonomy 独立降级空列表、belong_to_ready=false，
    不 500（与 graph labels 降级同型）。"""
    _patch_router_env(monkeypatch, tmp_path)
    fake = FakeConceptOpenSPG(errors={"get_reasoning_concepts", "get_concept_detail", "query_schema"})
    client = _router_client(monkeypatch, fake, _ADMIN)
    resp = client.get("/api/kag/projects/3/concept/rules", params={"type_name": "m0ProbeLive.Topic"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reasoning"] == []
    assert body["taxonomy"] == []
    assert body["concepts"] == []  # A-S3：queryConcept 失败 also 独立降级空
    assert body["belong_to_ready"] is False


def test_define_taxonomy_gated_on_belong_relation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2 实测门禁：schema 无 belongTo 属性时拒绝提交（避免 server 500 NPE）。"""
    _patch_router_env(monkeypatch, tmp_path)
    fake = FakeConceptOpenSPG(schema={"spgTypes": []})
    client = _router_client(monkeypatch, fake, _ADMIN)

    resp = client.post(
        "/api/kag/projects/3/concept/rules/define",
        json={"kind": "taxonomy", "concept_type_name": "m0ProbeLive.Topic", "concept_name": "1", "dsl": "x"},
    )
    assert resp.status_code == 400, resp.text
    assert fake.submitted == []  # 未触达上游

    fake2 = FakeConceptOpenSPG(schema=SCHEMA_WITH_BELONG)
    client2 = _router_client(monkeypatch, fake2, _ADMIN)
    resp = client2.post(
        "/api/kag/projects/3/concept/rules/define",
        json={"kind": "taxonomy", "concept_type_name": "m0ProbeLive.Topic", "concept_name": "1", "dsl": "x"},
    )
    assert resp.status_code == 200, resp.text
    assert fake2.submitted == [("define_taxonomy", "m0ProbeLive.Topic", "1", "x")]


def test_define_logical_and_remove_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    fake = FakeConceptOpenSPG(schema={})
    client = _router_client(monkeypatch, fake, _ADMIN)

    resp = client.post(
        "/api/kag/projects/3/concept/rules/define",
        json={
            "kind": "logical",
            "concept_type_name": "m0ProbeLive.Topic",
            "concept_name": "1",
            "predicate_name": "leadTo",
            "object_concept_type_name": "m0ProbeLive.Topic",
            "object_concept_name": "2",
            "dsl": "dsl-z",
        },
    )
    assert resp.status_code == 200, resp.text
    assert fake.submitted[0][0] == "define_logical"
    assert fake.submitted[0][1]["subject_concept_type_name"] == "m0ProbeLive.Topic"
    assert fake.submitted[0][1]["dsl"] == "dsl-z"

    resp = client.post(
        "/api/kag/projects/3/concept/rules/remove",
        json={
            "kind": "logical",
            "concept_type_name": "m0ProbeLive.Topic",
            "concept_name": "1",
            "object_concept_type_name": "m0ProbeLive.Topic",
            "object_concept_name": "2",
        },
    )
    assert resp.status_code == 200, resp.text
    assert fake.submitted[1][0] == "remove_logical"

    resp = client.post(
        "/api/kag/projects/3/concept/rules/remove",
        json={"kind": "taxonomy", "concept_type_name": "m0ProbeLive.Topic", "concept_name": "1"},
    )
    assert resp.status_code == 200, resp.text
    assert fake.submitted[2] == ("remove_taxonomy", "m0ProbeLive.Topic", "1")


def test_concept_rule_define_validation_and_cross_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    client = _router_client(monkeypatch, FakeConceptOpenSPG(schema={}), _ADMIN)

    # dsl 必填 / kind 非法（pydantic 422）
    assert (
        client.post(
            "/api/kag/projects/3/concept/rules/define",
            json={"kind": "taxonomy", "concept_type_name": "x", "concept_name": "1", "dsl": ""},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/kag/projects/3/concept/rules/define",
            json={"kind": "other", "concept_type_name": "x", "concept_name": "1", "dsl": "y"},
        ).status_code
        == 422
    )
    # 跨站写拒绝（same-origin 纵深防御）
    resp = client.post(
        "/api/kag/projects/3/concept/rules/define",
        json={"kind": "logical", "concept_type_name": "x", "dsl": "y"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403


def test_concept_rules_upstream_define_error_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Failing(FakeConceptOpenSPG):
        async def define_logical_causation(self, **kwargs):
            raise OpenSPGError("boom", status=500, body="NPE")

    _patch_router_env(monkeypatch, tmp_path)
    client = _router_client(monkeypatch, Failing(schema={}), _ADMIN)
    resp = client.post(
        "/api/kag/projects/3/concept/rules/define",
        json={
            "kind": "logical",
            "concept_type_name": "m0ProbeLive.Topic",
            "concept_name": "1",
            "object_concept_type_name": "m0ProbeLive.Topic",
            "object_concept_name": "2",
            "dsl": "y",
        },
    )
    assert resp.status_code == 502


def test_define_logical_requires_subject_concept_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """评审 P2：logical 定义主语概念名必填（与 remove 路径一致，无静默默认）。"""
    _patch_router_env(monkeypatch, tmp_path)
    fake = FakeConceptOpenSPG(schema={})
    client = _router_client(monkeypatch, fake, _ADMIN)
    resp = client.post(
        "/api/kag/projects/3/concept/rules/define",
        json={
            "kind": "logical",
            "concept_type_name": "m0ProbeLive.Topic",
            "object_concept_type_name": "m0ProbeLive.Topic",
            "object_concept_name": "2",
            "dsl": "y",
        },
    )
    assert resp.status_code == 400
    assert fake.submitted == []  # 未触达上游


# ---------------------------------------------------------------------------
# 概念层级树路由（附录 B.4；I3）：聚合 / 深度与节点截断 / 非概念类型 400 /
# 门禁 / 上游降级
# ---------------------------------------------------------------------------

# 模拟层级图：root=""/ROOT → [A, B]；A → [a1]；a1 → []
TREE_LEVELS = {
    "": {
        "children": [
            {"id": "A", "properties": {"nameZh": "甲", "name": "A"}},
            {"id": "B", "properties": {"name": "B"}},
        ]
    },
    "A": {"children": [{"id": "a1", "properties": {"nameZh": "乙", "name": "a1"}}]},
}


def test_get_concept_tree_builds_nested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """递归 BFS 聚合为嵌套树；nameZh 优先于 name，缺省回退空串。"""
    _patch_router_env(monkeypatch, tmp_path)
    client = _router_client(monkeypatch, FakeConceptOpenSPG(levels=TREE_LEVELS), _ADMIN)
    resp = client.get("/api/kag/projects/3/concepts/m0ProbeLive.Topic/tree")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type_name"] == "m0ProbeLive.Topic"
    assert body["root"] == ""
    assert body["nodes"] == 3
    assert body["truncated"] is False
    children = body["children"]
    assert children[0]["id"] == "A"
    assert children[0]["name"] == "甲"  # nameZh 优先
    assert children[0]["children"][0]["id"] == "a1"
    assert children[0]["children"][0]["name"] == "乙"
    assert children[1]["id"] == "B"
    assert children[1]["children"] == []


def test_get_concept_tree_honors_root_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """root 指定后仅从该概念展开（其 attach 过的子层不能再展开——非本样本叶）。"""
    _patch_router_env(monkeypatch, tmp_path)
    client = _router_client(monkeypatch, FakeConceptOpenSPG(levels=TREE_LEVELS), _ADMIN)
    resp = client.get(
        "/api/kag/projects/3/concepts/m0ProbeLive.Topic/tree", params={"root": "B"}
    )
    body = resp.json()
    assert body["root"] == "B"
    assert body["nodes"] == 0
    assert body["children"] == []  # B 无 children → 空子树


def test_get_concept_tree_depth_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """max_depth=1：只回顶层，子层不展开并标记 truncated。"""
    _patch_router_env(monkeypatch, tmp_path)
    client = _router_client(monkeypatch, FakeConceptOpenSPG(levels=TREE_LEVELS), _ADMIN)
    resp = client.get(
        "/api/kag/projects/3/concepts/m0ProbeLive.Topic/tree", params={"max_depth": 1}
    )
    body = resp.json()
    assert body["truncated"] is True
    assert body["nodes"] == 2
    assert body["children"][0]["children"] == []
    assert body["children"][1]["children"] == []


def test_get_concept_tree_node_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """max_nodes=1：超限即截断，残叶显式 node_cap_reached 标记。"""
    _patch_router_env(monkeypatch, tmp_path)
    client = _router_client(monkeypatch, FakeConceptOpenSPG(levels=TREE_LEVELS), _ADMIN)
    resp = client.get(
        "/api/kag/projects/3/concepts/m0ProbeLive.Topic/tree", params={"max_nodes": 1}
    )
    body = resp.json()
    assert body["truncated"] is True
    # A 被放行但子层 a1 超限 → 子层残叶标记
    assert body["children"][0]["id"] == "A"
    assert body["children"][0]["children"][0]["node_cap_reached"] is True


def test_get_concept_tree_not_concept_type_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """conceptType 非概念类型 → 归一 400（而非 502）。"""
    _patch_router_env(monkeypatch, tmp_path)

    class NotConceptType(FakeConceptOpenSPG):
        async def query_concept_level_instance(self, concept_type_name, root_concept_instance="", project_id=""):
            raise OpenSPGError("bad", status=400, body="Topic is not a concept type")

    client = _router_client(monkeypatch, NotConceptType(), _ADMIN)
    resp = client.get("/api/kag/projects/3/concepts/m0ProbeLive.X/tree")
    assert resp.status_code == 400
    assert resp.json()["detail"].endswith("is not a concept type")


def test_get_concept_tree_membership_and_upstream_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    # 非成员 → 403
    outsider = _router_client(monkeypatch, FakeConceptOpenSPG(), _OUTSIDER)
    assert (
        outsider.get("/api/kag/projects/3/concepts/m0ProbeLive.Topic/tree").status_code
        == 403
    )
    # 图不可达/上游错误 → 502
    failing = FakeConceptOpenSPG(errors={"query_concept_level_instance"})
    client = _router_client(monkeypatch, failing, _ADMIN)
    assert (
        client.get("/api/kag/projects/3/concepts/m0ProbeLive.Topic/tree").status_code
        == 502
    )
