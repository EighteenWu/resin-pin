from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

SETTINGS_KEYS = ("sync_interval_seconds",)

_file_lock = threading.Lock()


def load_state(path: str) -> dict[str, Any]:
    with _file_lock:
        return _read_unlocked(path)


def save_state(path: str, data: dict[str, Any]) -> None:
    with _file_lock:
        existing = _read_unlocked(path)
        out = dict(data)
        if not isinstance(out.get("nodes"), dict):
            out["nodes"] = existing.get("nodes") if isinstance(existing.get("nodes"), dict) else {}
        for key in SETTINGS_KEYS:
            if key in existing:
                out[key] = existing[key]
        _write_unlocked(path, out)


def patch_state(path: str, **fields: Any) -> dict[str, Any]:
    with _file_lock:
        data = _read_unlocked(path)
        data.update(fields)
        if not isinstance(data.get("nodes"), dict):
            data["nodes"] = {}
        _write_unlocked(path, data)
        return dict(data)


def _read_unlocked(path: str) -> dict[str, Any]:
    file = Path(path)
    if not file.exists():
        return {"nodes": {}}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"nodes": {}}
    if not isinstance(data, dict):
        return {"nodes": {}}
    nodes = data.get("nodes")
    if not isinstance(nodes, dict):
        data["nodes"] = {}
    return data


def _write_unlocked(path: str, data: dict[str, Any]) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, file)
