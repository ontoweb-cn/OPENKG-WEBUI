"""``/api/knowledge`` 薄代理（docs/plans/knowledge-center-phase-1a-tasks.md T3）：
启用门、身份注入（P1-1）、same-origin、透传与传输错误归一；以及
``/api/settings/knowledge`` 设置块的掩码与 tri-state 语义（T2）。

代理路由用独立 FastAPI app（不挂 _auth）直测路由逻辑；登录门在 main.py
挂载处，由架构测试与既有路由测试覆盖。
"""

from __future__ import annotations

import json
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
    # Phase 3 T2：连接与身份解析落在 provider 内（引擎抽象），夹具随之下移。
    from openkg_webui.services.knowledge import engines as engines_pkg
    from openkg_webui.services.knowledge.engines import intellect_rag as ir_engine
    import openkg_webui.services.knowledge as knowledge_service

    monkeypatch.setattr(
        knowledge_service,
        "resolve_upstream_connection",
        lambda: ("http://upstream.test", "svc-key"),
    )
    monkeypatch.setattr(
        knowledge_service,
        "resolve_request_auth",
        lambda user_id=None: ("svc-key", {"X-Intellect-User": "mem_u1"}),
    )
    # 兜底：provider 构造时不再接受外部 transport 注入以外的路径
    monkeypatch.setattr(ir_engine, "UPSTREAM_TIMEOUT", httpx.Timeout(5.0))
    del engines_pkg  # 仅用于导入校验
    app = FastAPI()
    app.include_router(knowledge_router.router, prefix="/api/knowledge-center")
    client = TestClient(app)
    client.calls = calls  # type: ignore[attr-defined]
    return client


_K = "/api/knowledge-center"


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

    # Phase 3 T2：身份解析在 provider 内（经 service 层），夹具指向新落点
    import openkg_webui.services.knowledge as knowledge_service

    monkeypatch.setattr(knowledge_service, "resolve_request_auth", _boom)
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
    # Phase 3 T2：provider 把传输层失败归一为 EngineError(UNREACHABLE) →
    # 路由层统一映射 502（错误码前缀保持 knowledge_upstream_*）
    assert "knowledge_upstream" in response.text


def test_identity_unavailable_maps_409(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _unavailable(user_id=None):
        raise KnowledgeIdentityUnavailable("identity expired")

    # Phase 3 T2：身份解析在 provider 内（经 service 层），夹具指向新落点
    import openkg_webui.services.knowledge as knowledge_service

    monkeypatch.setattr(knowledge_service, "resolve_request_auth", _unavailable)
    response = proxy.get(f"{_K}/datasets")
    assert response.status_code == 409
    assert "knowledge_identity_unavailable" in response.text


def test_upload_forwards_multipart(proxy: TestClient) -> None:
    response = proxy.post(
        f"{_K}/datasets/ds1/documents",
        files={"file": ("a.txt", b"hello", "text/plain")},
        data={"type": "local", "parent_path": "docs/sub"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    request = proxy.calls[0]  # type: ignore[attr-defined]
    assert request.method == "POST"
    assert str(request.url) == "http://upstream.test/api/v1/datasets/ds1/documents"
    body = request.content.decode("utf-8", "replace")
    # 上游读 getlist("file")——字段名必须单数（评审 R-1 回归守卫）
    assert 'name="file"' in body
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
    assert set(got) == {
        "version",
        "enabled",
        "base_url",
        "api_key",
        "api_key_set",
        "chat_scope",
        "mcp_url",
    }


def test_settings_normalize_defaults(settings_dir: Path) -> None:
    from openkg_webui.services.config.runtime_settings import RuntimeSettingsService as S

    block = S(settings_dir, process_env={})._normalize_system({})["knowledge"]
    assert block == {
        "version": 1,
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "chat_scope": "tenant",
        "mcp_url": "",
    }


# ---------------------------------------------------------------------------
# Phase 2：结构化上传 / GitHub 源 / SSE 日志流
# ---------------------------------------------------------------------------

import io as _io
import zipfile as _zipfile


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_structured_zip_groups_by_directory(proxy: TestClient) -> None:
    zip_bytes = _make_zip(
        {"docs/a/one.md": b"1", "docs/b/two.md": b"2", "__MACOSX/junk": b"x"}
    )
    response = proxy.post(
        f"{_K}/datasets/ds1/documents/structured",
        files={"file": ("bundle.zip", zip_bytes, "application/zip")},
        data={"type": "local"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    results = response.json()
    dirs = sorted(r["directory"] for r in results)
    assert dirs == ["docs/a", "docs/b"]


def test_structured_zip_slip_rejected(proxy: TestClient) -> None:
    evil = _make_zip({"../evil.txt": b"nope"})
    response = proxy.post(
        f"{_K}/datasets/ds1/documents/structured",
        files={"file": ("evil.zip", evil, "application/zip")},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 400
    assert "unsafe zip entry" in response.text


def test_github_source_put_masks_token(proxy: TestClient) -> None:
    response = proxy.put(
        f"{_K}/datasets/ds1/sources/github",
        json={"repo": "octocat/Hello-World", "branch": "main", "token": "ghp_secret"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["repo"] == "octocat/Hello-World"
    assert body.get("token_set") is True
    assert "ghp_secret" not in response.text

    got = proxy.get(f"{_K}/datasets/ds1/sources/github").json()
    assert got["token_set"] is True
    assert "ghp_secret" not in got.get("__repr__", "")


def test_logs_stream_smoke(proxy: TestClient) -> None:
    with proxy.stream("GET", f"{_K}/datasets/ds1/logs/stream?max_ticks=1") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        first = next(response.iter_lines())
        assert first.startswith("data:")


# ---------------------------------------------------------------------------
# Phase 2 T5：Web 爬取源（提取器 / 同站 BFS / 配置端点）
# ---------------------------------------------------------------------------

SAMPLE_HTML = """
<html><head><title>Docs Home</title></head><body>
<nav>ignore nav</nav>
<h1>Getting started</h1>
<p>First paragraph about 傅里叶变换.</p>
<ul><li>item one</li><li>item two</li></ul>
<pre>code block</pre>
<a href="/guide/advanced.html">Advanced</a>
<script>ignore()</script>
</body></html>
"""


def test_web_extractor_produces_markdown_and_links() -> None:
    from openkg_webui.services.knowledge.sources import web

    title, markdown, links = web.html_to_markdown(SAMPLE_HTML, "http://localhost:3300/docs/home")
    assert title == "Docs Home"
    assert "# Getting started" in markdown
    assert "First paragraph" in markdown
    assert "- item one" in markdown
    assert "```\ncode block" in markdown
    assert "ignore nav" not in markdown
    assert any(link.endswith("/guide/advanced.html") for link in links)


@pytest.mark.asyncio
async def test_web_crawl_bfs_same_site() -> None:
    import httpx as _httpx
    from openkg_webui.services.knowledge.sources import web

    pages = {
        "http://docs.test/home": (SAMPLE_HTML, "text/html"),
        "http://docs.test/guide/advanced.html": (
            "<html><head><title>Advanced</title></head><body><p>Advanced guide body</p></body></html>",
            "text/html",
        ),
    }

    def factory() -> _httpx.AsyncClient:
        def handler(request: _httpx.Request) -> _httpx.Response:
            key = str(request.url)
            body, ctype = pages.get(key, ("<html></html>", "text/html"))
            return _httpx.Response(200, text=body, headers={"content-type": ctype})

        return _httpx.AsyncClient(transport=_httpx.MockTransport(handler))

    result = await web.crawl_site(
        "http://docs.test/home", max_pages=5, max_depth=2, client_factory=factory
    )
    assert len(result) == 2
    assert {p.title for p in result} == {"Docs Home", "Advanced"}


def test_web_source_put_rejects_private_url(proxy: TestClient) -> None:
    response = proxy.put(
        f"{_K}/datasets/ds1/sources/web",
        json={"base_url": "http://127.0.0.1:9380"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# A5：knowledge MCP 注入单元验收（无激活 CLI 后端时的能力级验收）
# ---------------------------------------------------------------------------


def test_knowledge_mcp_config_writes_merged_servers(tmp_path) -> None:
    import json as _json

    import openkg_webui.services.knowledge as knowledge_service

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    # kag 先写入的 .mcp.json 不应被覆盖
    (workdir / ".mcp.json").write_text(
        _json.dumps({"mcpServers": {"kag-bridge": {"url": "http://x"}}}), encoding="utf-8"
    )
    monkey_settings = {"enabled": True, "mcp_url": "http://127.0.0.1:9382/mcp"}

    def fake_auth(user_id=None):
        return "svc-key", {"X-Intellect-User": "mem_u1"}

    orig_get = knowledge_service.get_knowledge_settings
    orig_auth = knowledge_service.resolve_request_auth
    knowledge_service.get_knowledge_settings = lambda: monkey_settings
    knowledge_service.resolve_request_auth = fake_auth
    try:
        knowledge_service.ensure_knowledge_mcp_config(str(workdir))
    finally:
        knowledge_service.get_knowledge_settings = orig_get
        knowledge_service.resolve_request_auth = orig_auth

    config = _json.loads((workdir / ".mcp.json").read_text(encoding="utf-8"))
    servers = config["mcpServers"]
    assert servers["kag-bridge"]["url"] == "http://x"
    assert servers["intellect-knowledge"]["url"] == "http://127.0.0.1:9382/mcp"
    assert servers["intellect-knowledge"]["headers"]["Authorization"] == "Bearer svc-key"

    claude = _json.loads((workdir / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "mcp__intellect-knowledge__intellect_retrieval" in claude["permissions"]["allow"]


def test_knowledge_mcp_config_skips_when_disabled(tmp_path) -> None:
    import openkg_webui.services.knowledge as knowledge_service

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    def fake_auth(user_id=None):
        raise AssertionError("should not resolve identity when disabled")

    orig_get = knowledge_service.get_knowledge_settings
    orig_auth = knowledge_service.resolve_request_auth
    knowledge_service.get_knowledge_settings = lambda: {"enabled": False, "mcp_url": "http://x"}
    knowledge_service.resolve_request_auth = fake_auth
    try:
        knowledge_service.ensure_knowledge_mcp_config(str(workdir))
        assert not list(workdir.iterdir())
    finally:
        knowledge_service.get_knowledge_settings = orig_get
        knowledge_service.resolve_request_auth = orig_auth


# ---------------------------------------------------------------------------
# 质量与安全评审回归（2026-09-22）
# ---------------------------------------------------------------------------


def test_structured_rejects_unsafe_rel_path(proxy: TestClient) -> None:
    """rel_paths 来自浏览器，必须与服务端 zip 条目同等清洗（防目录穿越）。"""
    response = proxy.post(
        f"{_K}/datasets/ds1/documents/structured",
        files={"file": ("a.txt", b"x", "text/plain")},
        data={"type": "local", "rel_paths": json.dumps(["../escape/a.txt"])},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 400
    assert "unsafe rel_path" in response.text


def test_structured_rejects_declared_oversize_entry(proxy: TestClient) -> None:
    """炸弹熔断在解压前触发（声明大小超限即拒绝）。"""
    import io as _io2
    import zipfile as _zf2

    from openkg_webui.api.routers import knowledge as _kr

    buf = _io2.BytesIO()
    with _zf2.ZipFile(buf, "w", compression=_zf2.ZIP_DEFLATED) as zf:
        # 高度可压缩的大内容：声明大小远超单文件上限
        zf.writestr("big.txt", b"0" * (_kr._ZIP_MAX_FILE_BYTES + 1024))
    response = proxy.post(
        f"{_K}/datasets/ds1/documents/structured",
        files={"file": ("bomb.zip", buf.getvalue(), "application/zip")},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 413


def test_structured_rejects_too_many_directories(proxy: TestClient) -> None:
    """目录数上限防止每个目录一次上游请求的放大。"""
    import io as _io3
    import zipfile as _zf3

    from openkg_webui.api.routers import knowledge as _kr

    buf = _io3.BytesIO()
    with _zf3.ZipFile(buf, "w") as zf:
        for i in range(_kr._ZIP_MAX_DIRS + 1):
            zf.writestr(f"d{i}/f.txt", b"x")
    response = proxy.post(
        f"{_K}/datasets/ds1/documents/structured",
        files={"file": ("many.zip", buf.getvalue(), "application/zip")},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 413
    assert "too many directories" in response.text


def test_web_crawl_blocks_redirect_to_private_host() -> None:
    """SSRF：公网 URL 302 到内网时不得跟随（原实现 follow_redirects=True 会绕过）。"""
    import asyncio as _asyncio

    import httpx as _httpx
    from openkg_webui.services.knowledge.sources import web

    def factory() -> _httpx.AsyncClient:
        def handler(request: _httpx.Request) -> _httpx.Response:
            if str(request.url).endswith("/start"):
                return _httpx.Response(
                    302, headers={"location": "http://127.0.0.1:9380/api/v1/datasets"}
                )
            return _httpx.Response(200, text="<html><body><p>secret</p></body></html>",
                                   headers={"content-type": "text/html"})

        return _httpx.AsyncClient(transport=_httpx.MockTransport(handler))

    # 同步测试中显式新建 loop（避免 pytest loop 复用告警）
    pages = _asyncio.new_event_loop().run_until_complete(
        web.crawl_site("http://docs.test/start", max_pages=3, client_factory=factory)
    )
    assert pages == []


def test_knowledge_store_file_is_0600(tmp_path, monkeypatch) -> None:
    """含 GitHub PAT 的 store 必须 0600 且原子写。"""
    import os
    import stat
    from pathlib import Path

    from openkg_webui.services import path_service
    from openkg_webui.services.knowledge.sources import store

    class _PS:
        user_data_dir = tmp_path

    monkeypatch.setattr(path_service, "get_path_service", lambda: _PS())
    store.set_source("u1", "ds1", {"repo": "a/b", "token": "ghp_secret"})
    path = Path(tmp_path) / "knowledge_sources.json"
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    assert "ghp_secret" not in store.get_source("u1", "ds1").values()
