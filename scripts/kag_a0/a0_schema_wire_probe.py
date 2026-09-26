# -*- coding: utf-8 -*-
"""A-S0：Schema 属性/类型 wire 契约拦包实测（方案 A 前置 gate）。

目标（docs/kag-integration-design.md §8.20 / 方案 A-S0）：
以 knext 客户端为权威消费方，拦包抓 /public/v1/schema/alterSchema 的真实 wire 形态
（M3.5 同款：拦截 ``SchemaApi.schema_alter_schema_post_with_http_info``，用
``sanitize_for_serialization`` 序列化后记录），作为 OPENKG-WebUI ``schema_draft.py``
新增构造器（属性 CREATE/UPDATE/DELETE、类型 CREATE、类型 DROP）的权威样本。

安全约束：
  - 探针项目默认 m2ReviewProj（id 4，含完整实体类型）；
  - 探针命名遵循 server 命名规则（type 名大写驼峰、属性名小写开头、均无下划线），
    并带上时间戳+pid 后缀避免与历史残留碰撞（见 NAMES / _suffix）；
  - 每个用例先幂等清理上一残留，commit 后重读 server 断言，finally 再还原 → 净零残留；
  - 单进程内新开 ``SchemaSession``（绕过 knext 进程内 schema 缓存），回读直连 REST
    （server 真值），规避 M3.5 已记录的“进程内缓存假阳性”。

用法：
  PYTHONPATH=/Users/simon/project/KAG \
  KAG_PROJECT_HOST_ADDR=http://127.0.0.1:8887 KAG_PROJECT_ID=4 \
  /tmp/kag-a0-venv/bin/python a0_schema_wire_probe.py [--only U1,U4]

结果：写 scripts/kag_a0/results/a0_schema_wire_probe.json
"""

import argparse
import json
import os
from pathlib import Path
import time
import traceback

HOST = os.environ.get("KAG_PROJECT_HOST_ADDR", "")
PROJECT_ID = int(os.environ.get("KAG_PROJECT_ID", "4"))
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# 探针命名：时间戳+pid 纯数字 suffix（杜绝与历史残留碰撞）。
# 命名规则（实测）：property 名 ^[a-z][0-9a-zA-Z]*（小写开头、无下划线）；
#   type 名 ^[A-Z][a-zA-Z0-9]*（大写开头、无点无下划线——故用驼峰大写 + 数字）。
_suffix = str(int(time.time() * 1000)) + str(os.getpid())
NAMES = {
    "prop": f"a0prop{_suffix}",
    "prop2": f"a0prop2{_suffix}",
    "type": f"A0Type{_suffix}",
    "type_zh": "探针实体类型",
    "child": f"A0Child{_suffix}",
}

# knext 的 basic_info.name.name 为带 namespace 全名（如 m2ReviewProj.Person）；
# 由 main() 从 server 探测 namespace，_full() 拼全名用于 session.get/存在性判断。
NS = ""


def _full(bare: str) -> str:
    return f"{NS}.{bare}" if NS else bare


# 拦包捕获容器
CAPTURED: list[dict] = []


def _short(text, n=800):
    text = text or ""
    return text if len(text) <= n else text[:n] + f"…<truncated {len(text) - n}>"


def install_hook():
    """拦 /schema/alterSchema：记录序列化 wire + http 结果，仍照常发送。"""
    from knext.schema.rest.schema_api import SchemaApi

    orig = SchemaApi.schema_alter_schema_post_with_http_info

    def patched(self, **kwargs):
        body_params = kwargs.get("schema_alter_request")
        wire = None
        if body_params is not None:
            try:
                wire = self.api_client.sanitize_for_serialization(body_params)
            except Exception:
                wire = None
        entry = {"op": "alter_schema", "wire": wire, "http": None, "resp": None}
        try:
            ret = orig(self, **kwargs)  # 成功即 200；ret 结构不定，不做下标访问
            entry["http"] = 200
            entry["resp"] = _short(repr(ret))
            return ret
        except Exception as exc:  # 记录服务端错误响应（DPO/级联拒绝等）
            entry["http"] = getattr(exc, "status", 0) or 0
            entry["resp"] = _short(repr(exc))
            entry["exc_body"] = _short(str(getattr(exc, "body", "") or ""))
            raise
        finally:
            CAPTURED.append(entry)

    SchemaApi.schema_alter_schema_post_with_http_info = patched


def _client():
    from knext.schema.client import SchemaClient

    return SchemaClient(host_addr=HOST, project_id=PROJECT_ID)


def _session(client):
    from knext.schema.client import SchemaSession

    return SchemaSession(client._rest_client, client._project_id)


def _rest_query(client):
    """直连 REST 回读 server 当前 schema（不受 session 缓存影响）。"""
    return client._rest_client.schema_query_project_schema_get(client._project_id)


def _is_name(nm, bare):
    """wire/read 的 basicInfo.name 匹配：全名、裸名、或 nameEn 皆算命中。"""
    if not isinstance(nm, dict):
        return False
    name = nm.get("name")
    name_en = nm.get("nameEn")
    return (
        name == bare
        or name_en == bare
        or str(name or "").endswith("." + bare)
        or str(name_en or "").endswith("." + bare)
    )


def _type_summary(rest_schema) -> dict:
    out = {}
    for t in rest_schema.spg_types or []:
        name = getattr(getattr(t, "basic_info", None), "name", None)
        full = getattr(name, "name", "") or getattr(name, "nameEn", "") or ""
        if not full:
            continue
        bare = full.split(".")[-1]
        props = [
            (p.basic_info.name.name, getattr(p, "alter_operation", None))
            for p in (t.properties or [])
        ]
        rels = [
            (r.basic_info.name.name, getattr(r, "alter_operation", None))
            for r in (t.relations or [])
        ]
        out[bare] = {"enum": t.spg_type_enum, "props": props, "rels": rels}
    return out


# —— wire 抽取辅助 ——


def _alter_types(wire):
    if not wire:
        return []
    return (wire.get("schemaDraft") or {}).get("alterSpgTypes") or []


def _prop_elements(wire, type_name):
    return [p for t in _type_elements(wire, type_name) for p in (t.get("properties") or [])]


def _type_elements(wire, type_name):
    out = []
    for t in _alter_types(wire):
        bi = (t.get("basicInfo") or {}).get("name") or {}
        if _is_name(bi, type_name):
            out.append(t)
    return out


def _elem_repr(elem):
    """精简元素 JSON（供归档可读）。"""
    return json.loads(json.dumps(elem, ensure_ascii=False, default=str))


# —— 幂等清理（还原）：删除探针属性 / 类型 ——


def _delete_probe_prop(client, names=NAMES):
    import knext.schema.model.base as B

    s = _session(client)
    person = s.get(_full("Person"))
    prop = person.properties.get(names["prop"])
    if prop is None:
        return
    prop.alter_operation = B.AlterOperationEnum.Delete
    s.update_type(person)
    s.commit()


def _delete_probe_type(client, type_name):
    s = _session(client)
    if _full(type_name) not in s.spg_types:
        return
    ent = s.get(_full(type_name))
    s.delete_type(ent)
    s.commit()


# —— 统一执行器 ——


def execute(key, commit_fn, assert_fn, cleanup_fn, only):
    if only and not any(key.startswith(p) for p in only):
        return None
    case = {"ok": False, "error": "", "detail": {}}
    try:
        CAPTURED.clear()
        c = _client()
        cleanup_fn(c)  # 幂等清理上一残留
        commit_fn(c)
        entry = CAPTURED[-1] if CAPTURED else {}
        wire = entry.get("wire")
        after = _type_summary(_rest_query(c))
        asserted = assert_fn(after, wire, entry) if assert_fn else True
        ok = bool(entry.get("http") == 200 and asserted)
        case["ok"] = ok
        case["detail"] = {
            "http": entry.get("http"),
            "resp": entry.get("resp"),
            "wire_alter_spg_types": [_elem_repr(t) for t in _alter_types(wire)],
            "server_after": after,
            "asserted": asserted,
        }
    except Exception as exc:
        case["error"] = _short(repr(exc) + "\n" + traceback.format_exc(), 1500)
        if CAPTURED:
            case["detail"] = {
                "http": CAPTURED[-1].get("http"),
                "resp": CAPTURED[-1].get("resp"),
                "exc_body": CAPTURED[-1].get("exc_body"),
                "wire_alter_spg_types": [
                    _elem_repr(t) for t in _alter_types(CAPTURED[-1].get("wire"))
                ],
                "capture_count": len(CAPTURED),
            }
    finally:
        try:
            if "c" in locals():
                cleanup_fn(c)
        except Exception as exc:
            # 还原失败 = 产生残留：无论断言如何，一律判红（"净零残留"由门禁保证）
            case["cleanup_error"] = _short(repr(exc), 500)
            case["ok"] = False
    return case


# —— 用例 ——


def _make_person_prop(client):
    """确保探针属性存在（幂等），返回 EntityType 的 Person rest 对象。"""
    from knext.schema.model.property import Property

    s = _session(client)
    person = s.get(_full("Person"))
    if person.properties.get(NAMES["prop"]) is None:
        person.add_property(
            Property(name=NAMES["prop"], object_type_name="Text", name_zh="探针属性")
        )
        s.update_type(person)
        s.commit()
    return person


# U1 属性 CREATE（.Text + NOT_NULL 约束）
def u1():
    from knext.schema.model.base import ConstraintTypeEnum
    from knext.schema.model.property import Property

    def commit(c):
        s = _session(c)
        person = s.get(_full("Person"))
        prop = Property(
            name=NAMES["prop"],
            object_type_name="Text",
            name_zh="探针属性",
            desc="A-S0 property",
        )
        prop.add_constraint(ConstraintTypeEnum.NotNull)
        person.add_property(prop)
        s.update_type(person)
        s.commit()

    def assert_(after, wire, entry):
        present = any(n == NAMES["prop"] for n, _ in after.get("Person", {}).get("props", []))
        target = next(
            (
                p
                for p in _prop_elements(wire, "Person")
                if _is_name((p.get("basicInfo") or {}).get("name") or {}, NAMES["prop"])
            ),
            None,
        )
        adv = (target or {}).get("advancedConfig") or {}
        has_created = bool(target and target.get("alterOperation") == "CREATE")
        has_notnull = any(
            (item.get("constraintTypeEnum") or "") == "NOT_NULL"
            for item in ((adv.get("constraint") or {}).get("constraintItems") or [])
        )
        return present and has_created and has_notnull

    return execute("U1_attr_create", commit, assert_, _delete_probe_prop, _ONLY)


# U2 属性 UPDATE（中文名/描述，元素级 UPDATE 配合类型 UPDATE）
def u2():

    def commit(c):
        _make_person_prop(c)
        s = _session(c)
        person = s.get(_full("Person"))
        prop = person.properties[NAMES["prop"]]
        prop.name_zh = "探针属性-改"
        prop.desc = "updated desc by A-S0"
        s.update_type(person)
        s.commit()

    def assert_(after, wire, entry):
        target = next(
            (
                p
                for p in _prop_elements(wire, "Person")
                if _is_name((p.get("basicInfo") or {}).get("name") or {}, NAMES["prop"])
            ),
            None,
        )
        bi = (target or {}).get("basicInfo") or {}
        # 属性 UPDATE = 类型整型覆写：knext 不给属性元素打变更标记（alterOperation=None），
        # 仅类型标记 UPDATE 并把更新后的值一并提交。断言 wire 已携带更新后的 nameZh/desc。
        type_update = any(
            t.get("alterOperation") == "UPDATE" for t in _type_elements(wire, "Person")
        )
        return bool(
            bi.get("nameZh") == "探针属性-改"
            and bi.get("desc") == "updated desc by A-S0"
            and type_update
        )

    return execute("U2_attr_update", commit, assert_, _delete_probe_prop, _ONLY)


# U3 属性 DELETE（元素级 alterOperation=DELETE；缺条目不等于删除的认识）
def u3():
    import knext.schema.model.base as B

    def commit(c):
        _make_person_prop(c)
        s = _session(c)
        person = s.get(_full("Person"))
        prop = person.properties[NAMES["prop"]]
        prop.alter_operation = B.AlterOperationEnum.Delete
        s.update_type(person)
        s.commit()

    def assert_(after, wire, entry):
        gone = not any(n == NAMES["prop"] for n, _ in after.get("Person", {}).get("props", []))
        target = next(
            (
                p
                for p in _prop_elements(wire, "Person")
                if _is_name((p.get("basicInfo") or {}).get("name") or {}, NAMES["prop"])
            ),
            None,
        )
        return gone and bool(target and target.get("alterOperation") == "DELETE")

    return execute("U3_attr_delete", commit, assert_, _delete_probe_prop, _ONLY)


# U4 类型 CREATE（EntityType，parent=Person，带探针属性）
def u4():
    from knext.schema.model.base import ConstraintTypeEnum
    from knext.schema.model.property import Property
    from knext.schema.model.spg_type import EntityType

    def commit(c):
        s = _session(c)
        ent = EntityType(
            name=_full(NAMES["type"]),
            name_zh=NAMES["type_zh"],
            desc="A-S0 probe entity type",
            parent_type_name=_full("Person"),
        )
        prop = Property(
            name=NAMES["prop2"],
            object_type_name="Text",
            name_zh="探针子属性",
            desc="A-S0 prop under new type",
        )
        prop.add_constraint(ConstraintTypeEnum.NotNull)
        ent.add_property(prop)
        s.create_type(ent)
        s.commit()

    def assert_(after, wire, entry):
        present = NAMES["type"] in after
        elem = None
        for t in _type_elements(wire, NAMES["type"]):
            elem = t
        has_create = bool(elem and elem.get("alterOperation") == "CREATE")
        has_enum = (elem or {}).get("spgTypeEnum") == "ENTITY_TYPE"
        return present and has_create and has_enum

    def cleanup(c):
        _delete_probe_type(c, NAMES["type"])

    return execute("U4_type_create", commit, assert_, cleanup, _ONLY)


# U5 类型 DELETE（DROP 刚建的探针类型）
def u5():
    from knext.schema.model.property import Property
    from knext.schema.model.spg_type import EntityType

    def ensure_type(c):
        s = _session(c)
        if _full(NAMES["type"]) in s.spg_types:
            return
        ent = EntityType(
            name=_full(NAMES["type"]),
            name_zh=NAMES["type_zh"],
            desc="A-S0 probe",
            parent_type_name=_full("Person"),
        )
        ent.add_property(
            Property(name=NAMES["prop2"], object_type_name="Text", name_zh="探针子属性")
        )
        s.create_type(ent)
        s.commit()

    def commit(c):
        ensure_type(c)
        s = _session(c)
        ent = s.get(_full(NAMES["type"]))
        s.delete_type(ent)
        s.commit()

    def assert_(after, wire, entry):
        gone = NAMES["type"] not in after
        elem = None
        for t in _type_elements(wire, NAMES["type"]):
            elem = t
        return gone and bool(elem and elem.get("alterOperation") == "DELETE")

    def cleanup(c):
        _delete_probe_type(c, NAMES["type"])

    return execute("U5_type_delete", commit, assert_, cleanup, _ONLY)


# U6 类型 DROP 的依赖行为探测（父子类型删除顺序）
# 关键发现（A-S0）：直接 drop 父类型会把子类型孤立为 parent=null 的孤儿，
# 且该孤儿 server 拒绝再 delete/update（"parent type can not be null"）→ 永久残留。
# 因此正解必须是「先删子类型、再删父类型」。本用例验证该正确顺序可净零。
def u6():
    from knext.schema.model.spg_type import EntityType

    def ensure_pair(c):
        s = _session(c)
        if _full(NAMES["type"]) not in s.spg_types:
            ent = EntityType(
                name=_full(NAMES["type"]),
                name_zh=NAMES["type_zh"],
                desc="A-S0",
                parent_type_name=_full("Person"),
            )
            s.create_type(ent)
            s.commit()
        s = _session(c)
        if _full(NAMES["child"]) not in s.spg_types:
            child = EntityType(
                name=_full(NAMES["child"]),
                name_zh="探针子类型",
                desc="A-S0 child",
                parent_type_name=_full(NAMES["type"]),
            )
            s.create_type(child)
            s.commit()

    def commit(c):
        ensure_pair(c)  # 建 A0Type + A0Child(parent=A0Type)
        # 顺序：先删子类型（parent 存在），再删父类型（已无子）→ 两者皆净零
        s = _session(c)
        s.delete_type(s.get(_full(NAMES["child"])))
        s.commit()
        s2 = _session(c)
        s2.delete_type(s2.get(_full(NAMES["type"])))
        s2.commit()
        decision = "child dropped first, then parent: both OK (净零)"
        import pathlib as _pl

        _pl.Path(RESULTS_DIR / "u6_drop_decision.txt").write_text(decision, "utf-8")

    def assert_(after, wire, entry):
        # 最后一个 wire（父类型 DELETE）为类型级 DROP 样本；断言父子均已从 server 消失
        elem = None
        for t in _type_elements(wire, NAMES["type"]):
            elem = t
        gone_child = NAMES["child"] not in after
        gone_parent = NAMES["type"] not in after
        decision = (
            (RESULTS_DIR / "u6_drop_decision.txt").read_text("utf-8")
            if (RESULTS_DIR / "u6_drop_decision.txt").exists()
            else ""
        )
        return bool(
            elem
            and elem.get("alterOperation") == "DELETE"
            and gone_child
            and gone_parent
            and decision
        )

    def cleanup(c):
        _delete_probe_type(c, NAMES["child"])
        _delete_probe_type(c, NAMES["type"])

    return execute("U6_drop_cascade", commit, assert_, cleanup, _ONLY)


_ONLY = set()


def main():
    global _ONLY
    ap = argparse.ArgumentParser(description="A-S0 schema wire probe")
    ap.add_argument("--only", default="", help="仅跑指定用例编号 U1..U6，逗号分隔")
    args = ap.parse_args()
    _ONLY = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if not HOST:
        raise SystemExit("请设置 KAG_PROJECT_HOST_ADDR（OpenSPG server 地址）")

    install_hook()
    global NS
    _probe_c = _client()
    for _t in _rest_query(_probe_c).spg_types or []:
        if str(getattr(_t, "spg_type_enum", "") or "") != "ENTITY_TYPE":
            continue
        _nm = (_t.basic_info.name.name) or ""
        if "." in _nm and not _nm.startswith("STD.") and not _nm.startswith("META."):
            NS = _nm.split(".")[0]
            break
    print(f"namespace 探测: {NS!r}")

    results = {"host": HOST, "project_id": PROJECT_ID, "names": NAMES, "cases": {}, "ok": False}
    for key, fn in [
        ("U1", u1),
        ("U2", u2),
        ("U3", u3),
        ("U4", u4),
        ("U5", u5),
        ("U6", u6),
    ]:
        case = fn()
        if case is None:
            continue
        results["cases"][key] = case
        print(
            f"[{key}] ok={case['ok']} http={case['detail'].get('http')} "
            f"err={case.get('error', '')[:160]}"
            + (
                f" cleanup_err={case.get('cleanup_error', '')[:120]}"
                if case.get("cleanup_error")
                else ""
            )
        )

    results["ok"] = all(v.get("ok") for v in results["cases"].values())
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "a0_schema_wire_probe.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), "utf-8"
    )
    print("\n=== 汇总 ===")
    for k, v in results["cases"].items():
        print(f"  {k}: {'PASS' if v['ok'] else 'FAIL'} {v.get('error', '')[:200]}")
    print(f"整体: {'ALL OK' if results['ok'] else '有 FAIL'} -> results/a0_schema_wire_probe.json")


if __name__ == "__main__":
    main()
