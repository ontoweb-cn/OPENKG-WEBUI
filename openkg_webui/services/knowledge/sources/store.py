# -*- coding: utf-8 -*-
"""外部源配置与同步状态存储（Phase 2 T4）。

JSON 文件：``<user_data_dir>/knowledge_sources.json``，键为
``"<user_id>|<dataset_id>"``。``token`` 为 write-only：读取端只回
``token_set`` 布尔，永不回显。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _store_path() -> Path:
    from openkg_webui.services.path_service import get_path_service

    return Path(get_path_service().user_data_dir) / "knowledge_sources.json"


def _key(user_id: str, dataset_id: str) -> str:
    return f"{user_id}|{dataset_id}"


def _load() -> dict[str, Any]:
    path = _store_path()
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict[str, Any]) -> None:
    """原子写 + 0600 权限（文件含 GitHub PAT，与知识中心 api_key 同级敏感）。"""
    import os
    import tempfile

    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".sources-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def get_source(user_id: str, dataset_id: str) -> dict[str, Any] | None:
    """读取源配置（``token`` 掩码为 ``token_set``，不回显）。"""
    entry = _load().get(_key(user_id, dataset_id))
    if not isinstance(entry, dict):
        return None
    cfg = dict(entry.get("config") or {})
    token_set = bool(cfg.pop("token", None))
    return {
        **cfg,
        "token_set": token_set,
        "state": dict(entry.get("state") or {}),
    }


def set_source(user_id: str, dataset_id: str, cfg: dict[str, Any]) -> None:
    key = _key(user_id, dataset_id)
    data = _load()
    entry = data.get(key) or {}
    old_cfg = entry.get("config") or {}
    token = str(cfg.get("token") or "").strip()
    if not token:
        # 省略 = 保留已存 token（tri-state，沿 api_key 先例）
        cfg["token"] = old_cfg.get("token") or ""
    data[key] = {"config": cfg, "state": dict(entry.get("state") or {})}
    _save(data)


def delete_source(user_id: str, dataset_id: str) -> bool:
    data = _load()
    key = _key(user_id, dataset_id)
    if key not in data:
        return False
    data.pop(key)
    _save(data)
    return True


def get_source_token(user_id: str, dataset_id: str) -> str:
    entry = _load().get(_key(user_id, dataset_id)) or {}
    return str((entry.get("config") or {}).get("token") or "")


def get_state(user_id: str, dataset_id: str) -> dict[str, Any]:
    entry = _load().get(_key(user_id, dataset_id)) or {}
    return dict(entry.get("state") or {})


def update_state(user_id: str, dataset_id: str, patch: dict[str, Any]) -> None:
    data = _load()
    key = _key(user_id, dataset_id)
    entry = data.get(key)
    if not isinstance(entry, dict):
        return
    entry["state"] = {**(entry.get("state") or {}), **patch}
    _save(data)


def set_status(user_id: str, dataset_id: str, status: str) -> None:
    data = _load()
    key = _key(user_id, dataset_id)
    entry = data.get(key)
    if isinstance(entry, dict):
        entry.setdefault("state", {})["sync_status"] = status
        _save(data)
