# -*- coding: utf-8 -*-
"""Web 爬取源引擎（Phase 2 T5）。

Derived from DeepMentor (deepmentor/services/web_source/{crawler,markdown,
html_extractor}.py, Apache-2.0), Copyright 2025 Data Intelligence Lab, The
University of Hong Kong. Modified: 精简移植——同站 BFS 抓取 + 轻量
HTML→Markdown 提取（html.parser，无外部解析依赖），产出与 GitHub 源同构的
``{路径: 内容哈希}`` 增量计划。

SSRF 防护：仅 http/https；拒绝 localhost 与私有/链路本地地址字面量。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import re
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_DEPTH = 2
DEFAULT_TIMEOUT_S = 15.0
MAX_PAGE_BYTES = 2 * 1024 * 1024

_SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
    ".json", ".xml", ".rss", ".woff", ".woff2", ".ttf", ".pdf", ".zip",
)

_BLOCKED_HOSTS = {"localhost"}
_BLOCKED_PREFIXES = ("127.", "10.", "192.168.", "169.254.", "172.16.", "172.17.",
                     "172.18.", "172.19.", "172.20.", "172.21.", "172.22.",
                     "172.23.", "172.24.", "172.25.", "172.26.", "172.27.",
                     "172.28.", "172.29.", "172.30.", "172.31.", "0.",)


def is_public_http_url(url: str) -> bool:
    """SSRF 字面量防护：仅 http/https 且主机非本地/私有地址。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in _BLOCKED_HOSTS or host.endswith(".local"):
        return False
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        return not any(host.startswith(p) for p in _BLOCKED_PREFIXES)
    return True


@dataclass
class CrawledPage:
    url: str
    title: str
    markdown: str
    content_hash: str


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _same_site(candidate: str, base_host: str) -> bool:
    try:
        parsed = urlparse(candidate)
        return parsed.scheme in {"http", "https"} and parsed.hostname == base_host
    except Exception:
        return False


def html_to_markdown(html: str, page_url: str) -> tuple[str, str, list[str]]:
    """提取 ``(标题, markdown, 同站链接)``——精简移植自 DeepMentor
    html_extractor/markdown（标题/段落/列表/代码块/换行）。"""
    from html.parser import HTMLParser

    links: list[str] = []
    title_parts: list[str] = []

    class _Extractor(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.out: list[str] = []
            self._skip = 0
            self._list_depth = 0
            self._in_title = False

        def handle_starttag(self, tag, attrs):  # noqa: ANN001
            if tag in {"script", "style", "nav", "header", "footer", "aside"}:
                self._skip += 1
                return
            if tag == "title":
                self._in_title = True
                return
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self.out.append("\n" + "#" * int(tag[1]) + " ")
            elif tag == "p":
                self.out.append("\n")
            elif tag == "li":
                self._list_depth += 1
                self.out.append("\n- ")
            elif tag == "pre":
                self.out.append("\n```\n")
            elif tag == "br":
                self.out.append("\n")
            elif tag == "a":
                href = dict(attrs).get("href")
                if href:
                    links.append(urljoin(page_url, href))

        def handle_endtag(self, tag):  # noqa: ANN001
            if tag in {"script", "style", "nav", "header", "footer", "aside"}:
                self._skip = max(0, self._skip - 1)
            elif tag == "title":
                self._in_title = False
            elif tag == "li":
                self._list_depth = max(0, self._list_depth - 1)
            elif tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "pre"}:
                self.out.append("\n")

        def handle_data(self, data):  # noqa: ANN001
            if self._in_title:
                title_parts.append(data)
                return
            if self._skip:
                return
            if data.strip():
                self.out.append(data)

    parser = _Extractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    title = re.sub(r"\s+", " ", "".join(title_parts)).strip() or page_url
    text = "".join(parser.out)
    markdown = re.sub(r"\n{3,}", "\n\n", text).strip()
    same_site = [
        link for link in links
        if _same_site(link, urlparse(page_url).hostname or "")
    ]
    return title, markdown, same_site


async def crawl_site(
    base_url: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    client_factory: Any = None,
) -> list[CrawledPage]:
    """同站 BFS 抓取（Derived from DeepMentor crawler.py，精简）。"""
    base = base_url.rstrip("/")
    if not is_public_http_url(base):
        raise ValueError(f"blocked or invalid base url: {base}")
    base_host = urlparse(base).hostname or ""

    seen: set[str] = {base}
    queue: deque[tuple[str, int]] = deque([(base, 0)])
    pages: list[CrawledPage] = []

    factory = client_factory or (lambda: httpx.AsyncClient(timeout=timeout_s, follow_redirects=True))
    async with factory() as client:
        while queue and len(pages) < max_pages:
            url, depth = queue.popleft()
            try:
                resp = await client.get(url)
            except httpx.HTTPError:
                continue
            if resp.status_code != 200:
                continue
            ctype = resp.headers.get("content-type", "")
            if "text/html" not in ctype:
                continue
            if len(resp.content) > MAX_PAGE_BYTES:
                continue
            title, markdown, links = html_to_markdown(resp.text, url)
            if not markdown:
                continue
            pages.append(
                CrawledPage(
                    url=url,
                    title=title,
                    markdown=markdown,
                    content_hash=_content_hash(markdown),
                )
            )
            if depth >= max_depth:
                continue
            for link in links:
                clean = urldefrag(link)[0]
                if clean in seen or not _same_site(clean, base_host):
                    continue
                if clean.lower().endswith(_SKIP_EXTENSIONS):
                    continue
                if not is_public_http_url(clean):
                    continue
                seen.add(clean)
                queue.append((clean, depth + 1))
    return pages
