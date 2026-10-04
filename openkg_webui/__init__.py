"""OPENKG-WebUI —— agent-native 框架。

本包被导入时顺带做一次进程级修正：对本机回环服务的调用不得被系统代理吞掉
（见 :func:`_bypass_proxy_for_loopback`）。这样三个入口（CLI / Web 服务 / SDK）
以及它们派生的子进程，都无需额外环境变量即可访问 127.0.0.1 上自部署的
OpenSPG、kag-bridge、本地模型/解析网关等。
"""

from __future__ import annotations

import os
from urllib.request import getproxies

#: 对 OPENKG-WebUI 而言恒等于「本机」的主机名——OpenSPG（:8887）、
#: kag-bridge（:8890）、本地模型/解析网关等自部署服务都在这上面。
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _bypass_proxy_for_loopback() -> None:
    """让系统代理设置不再影响对本机回环服务的调用。

    httpx 在 ``trust_env=True``（默认值）下经 ``urllib.request.getproxies()``
    解析代理；该函数在 macOS/Windows 上会读取**系统**代理，却不读系统代理的
    例外列表（ExceptionsList）。于是本机代理（如 Clash :7890）会收到本应发往
    127.0.0.1 的请求并回 502 —— 而 curl 只读环境变量、不读系统代理，所以在
    同一台机器上 curl 直连正常，本应用却报 502。

    ``getproxies()`` 一旦发现**任意** ``*_proxy`` 环境变量就不再参考系统设置，
    所以这里先把系统代理固化进 ``HTTP(S)_PROXY``：出网流量保持原样，``NO_PROXY``
    只把回环地址重新摘出来。子进程（agent CLI、前端 dev server）继承同一份环境，
    因而它们对 kag-bridge 等本机服务的调用同样受益。
    """
    system_proxies = getproxies()
    for scheme, env_key in (("http", "HTTP_PROXY"), ("https", "HTTPS_PROXY")):
        if not (os.environ.get(env_key) or os.environ.get(env_key.lower())):
            value = system_proxies.get(scheme)
            if value:
                os.environ[env_key] = value
    for env_key in ("NO_PROXY", "no_proxy"):
        hosts = [item.strip() for item in os.environ.get(env_key, "").split(",") if item.strip()]
        hosts.extend(host for host in _LOOPBACK_HOSTS if host not in hosts)
        os.environ[env_key] = ",".join(hosts)


_bypass_proxy_for_loopback()
