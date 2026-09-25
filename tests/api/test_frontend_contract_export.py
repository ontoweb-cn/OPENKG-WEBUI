from __future__ import annotations

import json

from openkg_webui.api.contracts.export import render_contracts, write_contracts


def _enum_values(schema: dict, name: str) -> set[str]:
    definition = schema["$defs"][name]
    return set(definition["enum"])


def test_frontend_contract_export_is_deterministic(tmp_path) -> None:
    first = render_contracts()
    second = render_contracts()

    assert first == second
    write_contracts(tmp_path)
    written = {
        path.name: path.read_text(encoding="utf-8") for path in sorted(tmp_path.glob("*.json"))
    }
    assert written == first
    for content in written.values():
        assert content.endswith("\n")
        assert (
            json.dumps(json.loads(content), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            == content
        )


def test_committed_contract_files_match_the_renderer() -> None:
    """提交的 web/contracts/schema/*.json 必须与渲染器一致（漂移守卫）。

    `test_frontend_contract_export_is_deterministic` 只比较 render 与 render，
    因此**已提交文件**与代码漂移时没有任何测试会失败——2026-09-25 评审实测：
    `KnowledgeDataset` 新增 `visibility` 后 openapi.json 落后一整个属性，而两个
    门禁（本测试与 `npm run contracts:check`，后者从已提交 JSON 反向生成 TS）
    都不报错。此测试把提交产物本身纳入断言。
    """
    from pathlib import Path

    schema_dir = Path(__file__).resolve().parents[2] / "web" / "contracts" / "schema"
    rendered = render_contracts()
    committed = {p.name: p.read_text(encoding="utf-8") for p in sorted(schema_dir.glob("*.json"))}

    assert committed, f"no contract files found under {schema_dir}"
    assert set(committed) == set(rendered), "contract file set drifted from the renderer"

    drifted = sorted(name for name, content in rendered.items() if committed.get(name) != content)
    assert not drifted, (
        "committed contracts are stale: "
        f"{drifted}. Regenerate with write_contracts(web/contracts/schema)."
    )


def test_turn_schema_contains_the_complete_v2_lifecycle() -> None:
    protocol = json.loads(render_contracts()["turn-protocol.json"])

    assert _enum_values(protocol, "TurnStatus") == {
        "queued",
        "running",
        "waiting_input",
        "completed",
        "failed",
        "cancelled",
    }
    assert "wait_for_input" in _enum_values(protocol, "StreamEventType")
    assert "worker_lost" in _enum_values(protocol, "TurnFailureCode")
    assert protocol["properties"]["protocol_version"]["default"] == "2.0"


def test_openapi_operation_ids_are_unique_for_type_generation() -> None:
    openapi = json.loads(render_contracts()["openapi.json"])
    operation_ids = [
        operation["operationId"]
        for path_item in openapi["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]

    assert len(operation_ids) == len(set(operation_ids))


def test_exported_runtime_contract_contains_no_secret_defaults() -> None:
    rendered = render_contracts()
    combined = "\n".join(rendered.values()).lower()

    assert "redis://" not in combined
    protocol = json.loads(rendered["turn-protocol.json"])
    runtime = protocol["$defs"]["RuntimeStatus"]
    assert "redis_url" not in runtime["properties"]
    assert "password" not in runtime["properties"]
    assert "token" not in runtime["properties"]


def test_check_mode_reports_drift_without_writing(tmp_path) -> None:
    write_contracts(tmp_path)
    target = tmp_path / "turn-protocol.json"
    target.write_text("{}\n", encoding="utf-8")

    changed = write_contracts(tmp_path, check=True)

    assert changed == ["turn-protocol.json"]
    assert target.read_text(encoding="utf-8") == "{}\n"
