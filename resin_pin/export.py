from __future__ import annotations

from typing import Any


def ready_items(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        url = (row.get("proxy_url") or "").strip()
        if not row.get("ready") or not url or url in seen:
            continue
        seen.add(url)
        items.append(
            {
                "proxyUrl": url,
                "region": str(row.get("region") or ""),
                "name": str(row.get("name") or ""),
            }
        )
    return items


def export_text(items: list[dict[str, str]]) -> str:
    return "\n".join(item["proxyUrl"] for item in items if item.get("proxyUrl"))


def export_json(items: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "items": items,
        "copy_all": export_text(items),
        "count": len(items),
    }
