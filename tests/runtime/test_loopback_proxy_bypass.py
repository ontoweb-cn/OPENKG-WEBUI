"""``openkg_webui`` 导入时的系统代理修正：回环直连、其余仍走代理。

修正在包导入时作用于进程环境，因此这里用子进程探测：只有全新的解释器才能
观察到「导入前 → 导入后」的环境变化。构造环境时清空所有 ``*_proxy``，避免
宿主机（可能配了系统代理）影响断言。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

_ROOT = Path(__file__).resolve().parents[2]

#: 子进程内导入包 → 打印修正后的环境与 httpx 实际路由决策。
#: ``_transport_for_url``/``_transport`` 是 httpx 私有属性，但正是它们决定了
#: 某次请求是否被送去代理，故直接断言其行为（httpx 0.28.x）。
_PROBE = """
import json, os
import openkg_webui  # noqa: F401  ← 触发修正
import httpx

client = httpx.AsyncClient(trust_env=True)

def relay(url):
    transport = client._transport_for_url(httpx.URL(url))
    return "direct" if transport is client._transport else "proxied"

print(json.dumps({
    "no_proxy": os.environ.get("NO_PROXY", ""),
    "no_proxy_lower": os.environ.get("no_proxy", ""),
    "http_proxy": os.environ.get("HTTP_PROXY", ""),
    "loopback": relay("http://127.0.0.1:8887/public/v1/project"),
    "external": relay("https://api.openai.com/v1/models"),
}))
"""


def _probe(extra_env: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
    env.update(extra_env)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_loopback_goes_direct_while_everything_else_stays_proxied() -> None:
    proxy = "http://proxy.example:3128"

    out = _probe({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy})

    assert {"127.0.0.1", "localhost", "::1"} <= set(out["no_proxy"].split(","))
    assert out["no_proxy_lower"] == out["no_proxy"]
    assert out["loopback"] == "direct"
    assert out["external"] == "proxied"


def test_operator_proxy_env_wins_over_the_system_proxy() -> None:
    out = _probe({"HTTP_PROXY": "http://operator.example:8080"})

    assert out["http_proxy"] == "http://operator.example:8080"
    assert out["loopback"] == "direct"


def test_existing_no_proxy_entries_are_kept() -> None:
    out = _probe({"NO_PROXY": "example.com"})

    hosts = out["no_proxy"].split(",")
    assert "example.com" in hosts
    assert "127.0.0.1" in hosts
