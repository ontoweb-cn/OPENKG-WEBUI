# -*- coding: utf-8 -*-
"""GitHub 源同步引擎（Phase 2 T4）。

Derived from DeepMentor (deepmentor/services/github_source/, Apache-2.0),
Copyright 2025 Data Intelligence Lab, The University of Hong Kong. Modified:
适配为"产出待上传内容"的上传计划引擎——不落地本地目录，变更条目由调用方
（知识代理）经 rag-app 上传链路写入。

仅依赖 httpx 的 GitHub REST 客户端（tree / commits / raw 下载），
``GITHUB_TOKEN`` 环境变量可选（匿名 60 req/h → 认证 5 000 req/h）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_TIMEOUT_S = 30.0
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单文件下载上限 2MB


@dataclass(frozen=True)
class TreeEntry:
    path: str
    sha: str
    type: str  # "blob" | "tree"


@dataclass(frozen=True)
class GitHubAPIError(Exception):
    status_code: int
    message: str

    def __str__(self) -> str:
        return f"GitHub API {self.status_code}: {self.message}"


def _headers(token: str | None) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "openkg-webui-knowledge/1.0",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def match_glob(path: str, glob: str) -> bool:
    return fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path.rsplit("/", 1)[-1], glob)


class GitHubClient:
    """Thin async wrapper around the handful of REST endpoints we need."""

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client_factory: Any = None,
    ) -> None:
        self._token = token
        self._timeout_s = timeout_s
        self._client_factory = client_factory

    async def _request_json(self, method: str, url: str, **kw: Any) -> Any:
        factory = self._client_factory or (lambda: httpx.AsyncClient(timeout=self._timeout_s))
        async with factory() as client:
            resp = await client.request(method, url, headers=_headers(self._token), **kw)
        if resp.status_code >= 400:
            try:
                msg = resp.json().get("message", resp.text[:500])
            except Exception:
                msg = resp.text[:500]
            raise GitHubAPIError(resp.status_code, str(msg))
        return resp.json()

    async def _request_bytes(self, url: str) -> bytes:
        factory = self._client_factory or (lambda: httpx.AsyncClient(timeout=self._timeout_s))
        async with factory() as client:
            resp = await client.get(url, headers=_headers(self._token))
        if resp.status_code >= 400:
            raise GitHubAPIError(resp.status_code, resp.text[:500])
        return resp.content

    async def get_latest_commit_sha(self, repo: str, branch: str) -> str:
        data = await self._request_json(
            "GET", f"{GITHUB_API_BASE}/repos/{repo}/commits/{branch}"
        )
        return data["sha"]

    async def get_tree(
        self,
        repo: str,
        branch: str,
        *,
        path_prefix: str = "",
        glob: str = "*.md",
    ) -> list[TreeEntry]:
        data = await self._request_json(
            "GET",
            f"{GITHUB_API_BASE}/repos/{repo}/git/trees/{branch}",
            params={"recursive": "1"},
        )
        entries: list[TreeEntry] = []
        prefix = path_prefix.strip("/")
        for item in data.get("tree", []):
            if item.get("type") != "blob":
                continue
            p = item.get("path", "")
            if prefix and not p.startswith(prefix + "/") and p != prefix:
                continue
            if not match_glob(p, glob):
                continue
            entries.append(TreeEntry(path=p, sha=item.get("sha", ""), type="blob"))
        return entries

    async def download_file(self, repo: str, path: str, ref: str) -> bytes:
        url = f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"
        content = await self._request_bytes(url)
        return content[:MAX_FILE_BYTES]


@dataclass
class SyncPlan:
    """一次同步待执行的上传/删除计划。"""

    head: str = ""
    uploads: list[tuple[str, bytes]] = field(default_factory=list)
    removals: list[str] = field(default_factory=list)
    skipped: bool = False


async def plan_sync(
    client: GitHubClient,
    *,
    repo: str,
    branch: str,
    path_prefix: str = "",
    glob: str = "*.md",
    prev_files: dict[str, str] | None = None,
) -> SyncPlan:
    """对比远端 tree 与上次同步的 ``{path: sha}``，产出上传/删除计划。

    ``prev_files`` 为空（首次同步）时全部条目视为新增。
    """
    prev_files = prev_files or {}
    head = await client.get_latest_commit_sha(repo, branch)
    if prev_files and head == prev_files.get("__head__"):
        return SyncPlan(head=head, skipped=True)

    entries = await client.get_tree(repo, branch, path_prefix=path_prefix, glob=glob)
    uploads: list[tuple[str, bytes]] = []
    removals: list[str] = []
    current: dict[str, str] = {}
    for entry in entries:
        current[entry.path] = entry.sha
        if prev_files.get(entry.path) != entry.sha:
            content = await client.download_file(repo, entry.path, branch)
            uploads.append((entry.path, content))
    for path in prev_files:
        if path not in current and path != "__head__":
            removals.append(path)
    return SyncPlan(head=head, uploads=uploads, removals=removals)
