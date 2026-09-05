from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load_state(path: str) -> dict[str, Any]:
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


def save_state(path: str, data: dict[str, Any]) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, file)
