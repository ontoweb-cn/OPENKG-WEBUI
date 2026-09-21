"""``/api/knowledge`` 薄代理（docs/plans/knowledge-center-phase-1a-tasks.md T3）：
启用门、身份注入（P1-1）、same-origin、透传与传输错误归一；以及
``/api/settings/knowledge`` 设置块的掩码与 tri-state 语义（T2）。

代理路由用独立 FastAPI app（不挂 _auth）直测路由逻辑；登录门在 main.py
挂载处，由架构测试与既有路由测试覆盖。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from openkg_webui.api import main as api_main
from openkg_webui.api.routers import knowledge as knowledge_router
from openkg_webui.api.routers import settings as settings_router
from openkg_webui.services.config.runtime_settings import RuntimeSettingsService
from openkg_webui.services.knowledge import KnowledgeIdentityUnavailable


# ---------------------------------------------------------------------------
# 代理路由
# ---------------------------------------------------------------------------


@pytest.fixture()
def proxy(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """知识代理 client：上游/身份全部打桩，transport 用 MockTransport 录制。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"code": 0, "data": []})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(knowledge_router, "_transport", transport)
    monkeypatch.setattr(knowledge_router, "_current_user", lambda: SimpleNamespace(id="u1"))
    monkeypatch.setattr(knowledge_router, "knowledge_enabled", lambda block=None: True)
    monkeypatch.setattr(
        knowledge_router,
        "resolve_upstream_connection",
        lambda: ("http://upstream.test", "svc-key"),
    )
    monkeypatch.setattr(
        knowledge_router,
        "resolve_request_auth",
        lambda user_id=None: ("svc-key", {"X-Intellect-User": "mem_u1"}),
    )
    app = FastAPI()
    app.include_router(knowledge_router.router, prefix="/api/knowledge")
    client = TestClient(app)
    client.calls = calls  # type: ignore[attr-defined]
    return client


_K = "/api/knowledge"


def test_disabled_returns_403(proxy: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge_router, "knowledge_enabled", lambda: False)
    response = proxy.get(f"{_K}/datasets")
    assert response.status_code == 403
    assert "not available" in response.text


def test_status_reports_enabled_and_identity(proxy: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = proxy.get(_K).json()
    assert payload == {"enabled": True, "identity_ok": True}

    def _boom(user_id=None):
        raise KnowledgeIdentityUnavailable("not linked")

    monkeypatch.setattr(knowledge_router, "resolve_request_auth", _boom)
    payload = proxy.get(_K).json()
    assert payload == {"enabled": True, "identity_ok": False}


def test_list_datasets_passthrough_with_identity(proxy: TestClient) -> None:
    response = proxy.get(f"{_K}/datasets", params={"page": 2, "page_size": 10})
    assert response.status_code == 200
    assert response.json() == {"code": 0, "data": []}
    request = proxy.calls[0]  # type: ignore[attr-defined]
    assert request.method == "GET"
    assert str(request.url) == "http://upstream.test/api/v1/datasets?page=2&page_size=10"
    assert request.headers["Authorization"] == "Bearer svc-key"
    assert request.headers["X-Intellect-User"] == "mem_u1"


def test_create_requires_same_origin(proxy: TestClient) -> None:
    # 跨站 Origin → 403，且不触达上游（无 Origin 的非浏览器请求按 origins
    # 模块语义放行，由会话鉴权负责）
    response = proxy.post(
        f"{_K}/datasets", json={"name": "kb"}, headers={"origin": "http://evil.test"}
    )
    assert response.status_code == 403
    assert proxy.calls == []  # type: ignore[attr-defined]

    # 同源请求放行（TestClient host=testserver）
    response = proxy.post(
        f"{_K}/datasets", json={"name": "kb"}, headers={"origin": "http://testserver"}
    )
    assert response.status_code == 200
    request = proxy.calls[0]  # type: ignore[attr-defined]
    assert request.method == "POST"
    assert str(request.url) == "http://upstream.test/api/v1/datasets"


def test_upstream_connection_error_maps_502(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(knowledge_router, "_transport", httpx.MockTransport(handler))
    response = proxy.get(f"{_K}/datasets")
    assert response.status_code == 502
    assert "knowledge_upstream_unreachable" in response.text


def test_identity_unavailable_maps_409(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _unavailable(user_id=None):
        raise KnowledgeIdentityUnavailable("identity expired")

    monkeypatch.setattr(knowledge_router, "resolve_request_auth", _unavailable)
    response = proxy.get(f"{_K}/datasets")
    assert response.status_code == 409
    assert "knowledge_identity_unavailable" in response.text


def test_upload_forwards_multipart(proxy: TestClient) -> None:
    response = proxy.post(
        f"{_K}/datasets/ds1/documents",
        files={"files": ("a.txt", b"hello", "text/plain")},
        data={"type": "local", "parent_path": "docs/sub"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    request = proxy.calls[0]  # type: ignore[attr-defined]
    assert request.method == "POST"
    assert str(request.url) == "http://upstream.test/api/v1/datasets/ds1/documents"
    body = request.content.decode("utf-8", "replace")
    assert "a.txt" in body and "hello" in body
    assert "parent_path" in body


# ---------------------------------------------------------------------------
# /api/settings/knowledge 设置块（T2）
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def admin_client(settings_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """管理员 client over 隔离 settings 目录（沿 agent-loop settings 测试夹具）。"""

    def _service() -> RuntimeSettingsService:
        return RuntimeSettingsService(settings_dir, process_env={})

    monkeypatch.setattr(settings_router, "get_runtime_settings_service", _service)
    monkeypatch.setattr(settings_router, "get_current_user", lambda: SimpleNamespace(is_admin=True))

    def _knowledge_settings() -> dict:
        system = _service().load_system()
        return dict(system.get("knowledge") or {})

    import openkg_webui.services.knowledge as knowledge_service

    monkeypatch.setattr(knowledge_service, "get_knowledge_settings", _knowledge_settings)
    return TestClient(api_main.app)


def _put(client: TestClient, **payload: object) -> dict:
    response = client.put(
        "/api/settings/knowledge", json=payload, headers={"origin": "http://testserver"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_settings_roundtrip_and_masking(admin_client: TestClient) -> None:
    saved = _put(
        admin_client,
        enabled=True,
        base_url="http://127.0.0.1:9380/",
        api_key="sk-inlect",
    )
    # base_url 去尾斜杠；api_key write-only 掩码
    assert saved["enabled"] is True
    assert saved["base_url"] == "http://127.0.0.1:9380"
    assert saved["api_key"] == ""
    assert saved["api_key_set"] is True

    # 省略 api_key = 保留已存值（tri-state，沿 kag 先例）
    saved = _put(admin_client, enabled=True)
    assert saved["api_key_set"] is True

    got = admin_client.get("/api/settings/knowledge").json()
    assert got["api_key"] == ""
    assert got["api_key_set"] is True
    assert set(got) == {"version", "enabled", "base_url", "api_key", "api_key_set"}


def test_settings_normalize_defaults() -> None:
    from openkg_webui.services.config.runtime_settings import RuntimeSettingsService as S

    block = S(Path("/nonexistent"), process_env={})._normalize_system({})["knowledge"]
    assert block == {"version": 1, "enabled": False, "base_url": "", "api_key": ""}
