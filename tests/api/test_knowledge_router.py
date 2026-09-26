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
    import openkg_webui.services.knowledge as knowledge_service
    from openkg_webui.services.knowledge import engines as engines_pkg
    from openkg_webui.services.knowledge.engines import intellect_rag as ir_engine

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


def test_status_reports_enabled_and_identity(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = proxy.get(_K).json()
    # 夹具只带 X-Intellect-User（无 Team/Project 头），故新建库实际落 private。
    # create_visibility 与创建时上游据以计算 visibility 的头同源。
    assert payload == {
        "enabled": True,
        "identity_ok": True,
        "create_visibility": "private",
    }

    def _boom(user_id=None):
        raise KnowledgeIdentityUnavailable("not linked")

    # Phase 3 T2：身份解析在 provider 内（经 service 层），夹具指向新落点
    import openkg_webui.services.knowledge as knowledge_service

    monkeypatch.setattr(knowledge_service, "resolve_request_auth", _boom)
    payload = proxy.get(_K).json()
    assert payload == {
        "enabled": True,
        "identity_ok": False,
        "create_visibility": "private",
    }


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"X-Intellect-Team": "team_1"}, "team"),
        # Project 头单独存在时上游同样落 visibility="project"（评审 P1-1：
        # 只查 Team 会漏判，UI 会错误地声称只能建私有库）
        ({"X-Intellect-Project": "proj_1"}, "project"),
        # Team 优先于 Project（上游 _compute_visibility 的分支顺序）
        ({"X-Intellect-Team": "t", "X-Intellect-Project": "p"}, "team"),
        # 空值不算（上游把空白头视为缺失）
        ({"X-Intellect-Team": "   "}, "private"),
    ],
)
def test_status_derives_create_visibility_from_identity_headers(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch, headers: dict, expected: str
) -> None:
    """新建库的实际范围由归因头决定，UI 据此陈述而非让用户选择。"""
    import openkg_webui.services.knowledge as knowledge_service

    monkeypatch.setattr(
        knowledge_service,
        "resolve_request_auth",
        lambda user_id=None: ("svc-key", headers),
    )
    assert proxy.get(_K).json()["create_visibility"] == expected


def test_dataset_visibility_normalization() -> None:
    """域模型出站 visibility：优先权威列，legacy 行按**归属 id** 推导。"""
    from openkg_webui.services.knowledge.engines.intellect_rag import _dataset_visibility

    assert _dataset_visibility({"visibility": "tenant"}) == "tenant"
    assert _dataset_visibility({"visibility": "private"}) == "private"
    # legacy 行（无 visibility）：有 team_id/project_id 说明归属明确，按之
    # 上报——若一律落 tenant 会比实际更宽（评审 P2-3）
    assert _dataset_visibility({"permission": "team", "team_id": "t1"}) == "team"
    assert _dataset_visibility({"permission": "team", "project_id": "p1"}) == "project"
    # 无归属 id 的 legacy team 行在上游是租户可见，不能显示成团队
    assert _dataset_visibility({"permission": "team"}) == "tenant"
    assert _dataset_visibility({"permission": "team", "team_id": "  "}) == "tenant"
    assert _dataset_visibility({"permission": "me"}) == "private"
    # 未知/缺失一律保守为 private
    assert _dataset_visibility({}) == "private"
    assert _dataset_visibility({"visibility": "shared-ish"}) == "private"


def test_list_datasets_passthrough_with_identity(proxy: TestClient) -> None:
    response = proxy.get(f"{_K}/datasets", params={"page": 2, "page_size": 10})
    assert response.status_code == 200
    # T3：出站为域形状（信封在 provider 内被解包）
    assert response.json() == {"datasets": [], "total": 0}
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


def test_create_clamps_team_permission_without_team_identity(proxy: TestClient) -> None:
    """身份无 team/project 上下文时，请求里的 team 落为 me（评审 P1-1）。

    否则上游会把 visibility 落成 private，而 legacy permission 却写着 team，
    留下"看着是团队、实际私有"的记录。
    """
    response = proxy.post(
        f"{_K}/datasets",
        json={"name": "kb", "permission": "team"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    body = json.loads(proxy.calls[-1].content)  # type: ignore[attr-defined]
    assert body["permission"] == "me"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"X-Intellect-Team": "team_1"}, "team"),
        # Project 头单独存在时上游也落 project —— 只查 Team 会误判为 me
        ({"X-Intellect-Project": "proj_1"}, "team"),
    ],
)
def test_create_reports_actual_scope_from_identity(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch, headers: dict, expected: str
) -> None:
    """有 Team/Project 头时上游必落 team/project，legacy permission 如实上报。

    请求里写 me 也不改变上游结果（它忽略 permission），所以这里上报 team 而
    非用户所填——这正是评审 P1-1 指出的"选私有却建出共享库"的反向情形。
    """
    import openkg_webui.services.knowledge as knowledge_service

    monkeypatch.setattr(
        knowledge_service,
        "resolve_request_auth",
        lambda user_id=None: ("svc-key", headers),
    )
    response = proxy.post(
        f"{_K}/datasets",
        json={"name": "kb", "permission": "me"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    body = json.loads(proxy.calls[-1].content)  # type: ignore[attr-defined]
    assert body["permission"] == expected


def test_create_rejects_unknown_permission(proxy: TestClient) -> None:
    """非法 permission 退回 400，不再静默折叠成 me 后"成功"建库（评审 P3-4）。"""
    response = proxy.post(
        f"{_K}/datasets",
        json={"name": "kb", "permission": "public"},
    )
    assert response.status_code == 400
    assert proxy.calls == []  # type: ignore[attr-defined]  # 未触达上游


def test_update_dataset_roundtrip_reads_back(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-T8：PUT 部分更新后必须回读——响应以服务端状态为准（R3）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            assert json.loads(request.content) == {"name": "Renamed"}
            return httpx.Response(200, json={"code": 0, "data": True})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "id": "kb1",
                    "name": "Renamed",
                    "description": "",
                    "permission": "me",
                    "document_count": 2,
                    "chunk_count": 5,
                    "token_count": 7,
                    "created_at": "123",
                },
            },
        )

    monkeypatch.setattr(knowledge_router, "_transport", httpx.MockTransport(handler))
    response = proxy.put(
        f"{_K}/datasets/kb1",
        json={"name": "Renamed"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["document_count"] == 2


def test_update_dataset_validations(proxy: TestClient) -> None:
    """空 body / 空白 name 在本层 400，不触达上游。"""
    empty = proxy.put(
        f"{_K}/datasets/kb1", json={}, headers={"origin": "http://testserver"}
    )
    assert empty.status_code == 400
    blank = proxy.put(
        f"{_K}/datasets/kb1", json={"name": "   "}, headers={"origin": "http://testserver"}
    )
    assert blank.status_code == 400
    assert proxy.calls == []  # type: ignore[attr-defined]


def test_upstream_connection_error_maps_502(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(knowledge_router, "_transport", httpx.MockTransport(handler))
    response = proxy.get(f"{_K}/datasets")
    assert response.status_code == 502
    # T3：传输层失败 → EngineError(UNREACHABLE) → 路由映射 502，
    # detail 前缀为统一 kind（knowledge_unreachable）
    assert "knowledge_unreachable" in response.text


def test_identity_unavailable_maps_409(proxy: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
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
    zip_bytes = _make_zip({"docs/a/one.md": b"1", "docs/b/two.md": b"2", "__MACOSX/junk": b"x"})
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
    # T3：聚合为域形状（uploaded/error），不再有 nested upstream 信封
    for entry in results:
        assert set(entry) == {"directory", "uploaded", "error"}


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
            return _httpx.Response(
                200,
                text="<html><body><p>secret</p></body></html>",
                headers={"content-type": "text/html"},
            )

        return _httpx.AsyncClient(transport=_httpx.MockTransport(handler))

    # 同步测试中显式新建 loop（避免 pytest loop 复用告警）
    pages = _asyncio.new_event_loop().run_until_complete(
        web.crawl_site("http://docs.test/start", max_pages=3, client_factory=factory)
    )
    assert pages == []


def test_knowledge_store_file_is_0600(tmp_path, monkeypatch) -> None:
    """含 GitHub PAT 的 store 必须 0600 且原子写。"""
    import os
    from pathlib import Path
    import stat

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


# ---------------------------------------------------------------------------
# Phase 3 T3：错误映射（403/404/400/502）与流式契约
# ---------------------------------------------------------------------------


def _stub_upstream(monkeypatch, payload: dict, status_code: int = 200):
    """让上游返回指定信封/状态码（用于错误映射断言）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    monkeypatch.setattr(knowledge_router, "_transport", httpx.MockTransport(handler))


def test_upstream_permission_error_maps_403(proxy: TestClient, monkeypatch) -> None:
    """上游 102 + 权限语义 → 403（评审 P1：102 重载码按 message 细分）。"""
    _stub_upstream(monkeypatch, {"code": 102, "message": "No authorization."})
    response = proxy.get(f"{_K}/datasets")
    assert response.status_code == 403
    assert "knowledge_unauthorized" in response.text


def test_upstream_permission_retcode_maps_403(proxy: TestClient, monkeypatch) -> None:
    """RetCode 108（PERMISSION_ERROR）→ 403。"""
    _stub_upstream(monkeypatch, {"code": 108, "message": "denied"})
    response = proxy.get(f"{_K}/datasets")
    assert response.status_code == 403


def test_upstream_not_found_maps_404(proxy: TestClient, monkeypatch) -> None:
    """上游 102 + not found 语义 → 404。"""
    _stub_upstream(monkeypatch, {"code": 102, "message": "Document not found!"})
    response = proxy.get(f"{_K}/datasets/ds-x/documents/doc-y")
    assert response.status_code == 404
    assert "knowledge_not_found" in response.text


def test_upstream_invalid_maps_400(proxy: TestClient, monkeypatch) -> None:
    """上游 101（ARGUMENT_ERROR）→ 400。"""
    _stub_upstream(monkeypatch, {"code": 101, "message": "Invalid filename."})
    response = proxy.get(f"{_K}/datasets")
    assert response.status_code == 400
    assert "knowledge_invalid" in response.text


def test_upstream_http_403_maps_403(proxy: TestClient, monkeypatch) -> None:
    """HTTP 层 403（无信封）→ 403。"""
    _stub_upstream(monkeypatch, {"detail": "forbidden"}, status_code=403)
    response = proxy.get(f"{_K}/datasets")
    assert response.status_code == 403


def test_upstream_http_500_maps_502(proxy: TestClient, monkeypatch) -> None:
    """HTTP 5xx → 502。"""
    _stub_upstream(monkeypatch, {"error": "boom"}, status_code=500)
    response = proxy.get(f"{_K}/datasets")
    assert response.status_code == 502


def test_direct_upload_streams_file_like(proxy: TestClient) -> None:
    """D4 流式契约：直传路径必须传 file-like（不整体读入内存）。

    断言 MockTransport 收到的 multipart 中文件部分来自文件对象——
    以 httpx 是否成功编码流式文件为准（若实现改为 .read() 收口 bytes，
    本测试仍会通过，故另用体积断言辅证：见 test_large_upload_not_buffered）。
    """
    payload = b"x" * (3 * 1024 * 1024)  # 3MB
    response = proxy.post(
        f"{_K}/datasets/ds1/documents",
        files={"file": ("big.bin", payload, "application/octet-stream")},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    request = proxy.calls[-1]  # type: ignore[attr-defined]
    assert request.method == "POST"
    assert len(request.content) >= len(payload)  # 已转发且完整


def test_large_upload_uses_spooled_file_not_bytes(monkeypatch, tmp_path) -> None:
    """D4 反向守卫：provider 收到的 content 必须是 file-like（非 bytes）。

    通过直接调用 provider.upload 并捕获实参实现——若将来有人把
    UploadFile.file 改成 await f.read()，本断言失败。
    """
    import asyncio

    from openkg_webui.services.knowledge import engines as eng

    captured: dict[str, object] = {}
    engine = eng.build_engine()

    class _Spooled:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self, *args):  # noqa: ANN002
            return self._data

    async def fake_request(method: str, path: str, **kwargs):  # noqa: ANN003
        captured.update(kwargs)
        raise eng.EngineError(eng.EngineErrorKind.UNREACHABLE, "stub")

    monkeypatch.setattr(engine, "request", fake_request)
    item = eng.UploadItem(name="a.bin", content=_Spooled(b"data"))  # type: ignore[arg-type]

    async def _run() -> None:
        try:
            await engine.upload("ds1", [item])
        except eng.EngineError:
            pass

    # asyncio.run 新建独立 loop——get_event_loop 在前序测试关闭 loop 后会抛
    # RuntimeError（Python 3.12 行为）
    asyncio.run(_run())
    files = captured.get("files") or []
    assert files, "upload did not forward files"
    forwarded = files[0][1][1]
    assert not isinstance(forwarded, bytes), (
        "D4 violation: upload buffered the file into bytes instead of streaming"
    )


# ---------------------------------------------------------------------------
# P3 T13'：嵌入模型清单 / 兼容性检查 / 换模型强制检查
# ---------------------------------------------------------------------------


def test_embedding_models_filters_by_type(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/api/v1/models")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": [
                    {
                        "name": "qwen3-embedding-4b@default@GPUStack",
                        "provider_name": "GPUStack",
                        "model_type": ["embedding"],
                    },
                    {
                        "name": "Qwen3-Reranker-8B",
                        "provider_name": "GPUStack",
                        "model_type": ["rerank"],
                    },
                ],
            },
        )

    monkeypatch.setattr(knowledge_router, "_transport", httpx.MockTransport(handler))
    response = proxy.get(f"{_K}/models")
    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert names == ["qwen3-embedding-4b@default@GPUStack"]


def test_check_embedding_compatible(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "model": "m",
                    "sampled": 5,
                    "valid": 5,
                    "avg_cos_sim": 0.93,
                    "min_cos_sim": 0.9,
                    "max_cos_sim": 0.97,
                    "match_mode": "content_only",
                },
            },
        )

    monkeypatch.setattr(knowledge_router, "_transport", httpx.MockTransport(handler))
    response = proxy.post(
        f"{_K}/datasets/kb1/embedding/check",
        json={"embd_id": "m"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["compatible"] is True
    assert body["avg_cos_sim"] == 0.93


def test_check_dimension_mismatch_is_a_conclusion(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """维度不匹配是检查的**结论**而非服务端故障——归一 compatible=False（R9）。"""
    handler_called: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        handler_called.append(True)
        return httpx.Response(
            200,
            json={
                "code": 102,
                "message": (
                    "Embedding failure. The dimension (1024) of given embedding "
                    "model is different from the original (768)"
                ),
            },
        )

    monkeypatch.setattr(knowledge_router, "_transport", httpx.MockTransport(handler))
    response = proxy.post(
        f"{_K}/datasets/kb1/embedding/check",
        json={"embd_id": "m"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["compatible"] is False
    assert "dimension" in body["reason"].lower()


def test_update_embedding_model_enforces_check(
    proxy: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D5：PUT embedding_model 时服务端自跑检查，不兼容 409 且不触达更新。"""
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "model": "new-embd",
                        "sampled": 5,
                        "valid": 5,
                        "avg_cos_sim": 0.2,
                        "min_cos_sim": 0.1,
                        "max_cos_sim": 0.3,
                        "match_mode": "content_only",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "id": "kb1",
                    "name": "n",
                    "description": "",
                    "permission": "me",
                    "document_count": 0,
                    "chunk_count": 0,
                    "token_num": 0,
                    "create_time": "1",
                },
            },
        )

    monkeypatch.setattr(knowledge_router, "_transport", httpx.MockTransport(handler))
    response = proxy.put(
        f"{_K}/datasets/kb1",
        json={"embedding_model": "new-embd"},
        headers={"origin": "http://testserver"},
    )
    assert response.status_code == 409
    assert "embedding_incompatible" in response.text
    assert "PUT" not in methods  # 不兼容：未触达上游更新
