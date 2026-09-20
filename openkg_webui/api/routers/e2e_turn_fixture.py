"""``/__e2e__/v2-turn-runtime`` controller for the multi-worker browser audit.

Registered only when ``OPENKG_WEBUI_MULTI_WORKER_E2E=1`` (see
``openkg_webui/api/main.py``). Wire contract mirrors
``web/tests/e2e/fixtures/runtime.ts`` exactly:

* GET  /health                     -> {"worker_ids": [...]}
* POST /reset                      -> clear scenarios + re-register workers
* POST /scenarios                  -> arm one scenario -> {scenario_id, prompt}
* POST /scenarios/{id}/actions     -> drop_socket | kill_owner |
                                       pause_after_checkpoint |
                                       query_from_observer
* GET  /scenarios/{id}/evidence    -> ScenarioEvidence
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from openkg_webui.services.e2e_fixture import ENABLED, get_fixture

router = APIRouter()


def _fixture() -> Any:
    fixture = get_fixture()
    if fixture is None:
        raise HTTPException(status_code=404, detail="e2e fixture not enabled")
    return fixture


@router.get("/health")
async def health() -> dict[str, Any]:
    fixture = _fixture()
    workers = await fixture.live_workers()
    return {"worker_ids": [w["worker_id"] for w in workers]}


@router.post("/reset")
async def reset() -> dict[str, Any]:
    fixture = _fixture()
    await fixture.reset()
    return {"ok": True}


@router.post("/scenarios")
async def arm_scenario(body: dict[str, Any]) -> dict[str, Any]:
    fixture = _fixture()
    scenario = str(body.get("scenario") or "")
    params = body.get("params") if isinstance(body.get("params"), dict) else {
        key: value for key, value in body.items() if key != "scenario"
    }
    if not scenario:
        raise HTTPException(status_code=400, detail="scenario is required")
    return await fixture.arm(scenario, params)


@router.post("/scenarios/{scenario_id}/actions")
async def run_action(scenario_id: str, body: dict[str, Any]) -> dict[str, Any]:
    fixture = _fixture()
    action = str(body.get("action") or "")
    if action == "kill_owner":
        # The lease owner is a uvicorn worker process — possibly the very one
        # serving this request. Respond first, then kill in the background so
        # the client never sees a socket hang up.
        asyncio.create_task(fixture.kill_owner(scenario_id))
        return {"ok": True}
    handler = {
        "drop_socket": fixture.drop_socket,
        "pause_after_checkpoint": fixture.pause_after_checkpoint,
        "query_from_observer": fixture.query_from_observer,
    }.get(action)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"unknown action: {action}")
    await handler(scenario_id)
    return {"ok": True}


@router.get("/scenarios/{scenario_id}/evidence")
async def evidence(scenario_id: str) -> dict[str, Any]:
    fixture = _fixture()
    return await fixture.evidence(scenario_id)


__all__ = ["router"]
