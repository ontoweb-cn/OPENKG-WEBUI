"""`resolve_request_auth` 的主体钉定回归测试（可见范围修复 PR-7 / P0-B）。

背景：token 模式若不带 ``X-Intellect-User``，rag-app 侧的主体身份会退化为
``X-Intellect-Tenant`` 的解析值——owner 短路与取数租户集合都会用错身份。
本组用例锁定补发行为及其边界（不覆盖 provider 的 header 形态，那是
agent-loop identity 的既有测试范围）。

夹具为轻量替身：只替换 identity 解析与连接解析两处边界，
被测函数本身是真实的。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from openkg_webui.services import knowledge as ksvc


@dataclass
class _Identity:
    api_key: str = ""
    headers: dict = field(default_factory=dict)
    member_id: str = ""


@pytest.fixture()
def auth_env(monkeypatch: pytest.MonkeyPatch):
    """返回装配器：设定 identity_mode / identity 替身，调用真实函数。"""

    def _setup(*, mode: str, identity: _Identity):
        from openkg_webui.services.agent_loop import identity as ident_mod
        from openkg_webui.services.agent_loop import settings as settings_mod

        monkeypatch.setattr(ksvc, "resolve_upstream_connection", lambda: ("http://up", "svc-key"))
        monkeypatch.setattr(
            settings_mod, "resolve_primary_profile", lambda: {"identity_mode": mode}
        )
        monkeypatch.setattr(ident_mod, "resolve_backend_identity", lambda *a, **k: identity)
        return ksvc.resolve_request_auth("local-admin")

    return _setup


def test_token_mode_sends_own_member_id_as_subject(auth_env) -> None:
    """token 模式：Bearer 是成员令牌，且补发该 token 自己的 member_id。"""
    bearer, headers = auth_env(
        mode="token",
        identity=_Identity(api_key="imt_abc", member_id="local-admin"),
    )
    assert bearer == "imt_abc"
    assert headers["X-Intellect-User"] == "local-admin"


def test_token_required_mode_also_pins_subject(auth_env) -> None:
    bearer, headers = auth_env(
        mode="token_required",
        identity=_Identity(api_key="imt_abc", member_id="mem_local"),
    )
    assert bearer == "imt_abc"
    assert headers["X-Intellect-User"] == "mem_local"


def test_header_mode_still_uses_service_key(auth_env) -> None:
    """header/off 模式行为不变：服务 key + 归因头（不因本次改动回归）。"""
    bearer, headers = auth_env(
        mode="header",
        identity=_Identity(api_key="", headers={"X-Intellect-User": "mem_u1"}, member_id="mem_u1"),
    )
    assert bearer == "svc-key"
    assert headers["X-Intellect-User"] == "mem_u1"


def test_existing_subject_header_is_not_overwritten(auth_env) -> None:
    """已存在的 X-Intellect-User（不同大小写）不被覆盖，避免双值。"""
    bearer, headers = auth_env(
        mode="token",
        identity=_Identity(
            api_key="imt_abc",
            headers={"x-intellect-user": "keep-me"},
            member_id="local-admin",
        ),
    )
    assert bearer == "imt_abc"
    assert headers == {"x-intellect-user": "keep-me"}


def test_missing_member_id_omits_header(auth_env) -> None:
    """member_id 缺失时不写空头（上游把空白头视为缺失，写空无意义）。"""
    bearer, headers = auth_env(
        mode="token",
        identity=_Identity(api_key="imt_abc", member_id=""),
    )
    assert bearer == "imt_abc"
    assert headers == {}


def test_token_mode_without_bearer_raises(auth_env) -> None:
    with pytest.raises(ksvc.KnowledgeIdentityUnavailable):
        auth_env(mode="token", identity=_Identity(api_key="", member_id="local-admin"))


def test_header_literal_matches_identity_module() -> None:
    """漂移守卫：本模块的字面量必须等于 identity.py 的私有常量。

    知识中心刻意复用字面量而不导入私有名（评审 P3-5）；若 identity.py 改名而
    此处未跟随，补发的头会被上游当作未知头忽略，owner 短路静默失效——
    这个断言让那种漂移在测试里失败，而不是在生产里静默。
    """
    from openkg_webui.services.agent_loop import identity as ident

    assert ksvc._IDENTITY_USER_HEADER == ident._HEADER_USER
