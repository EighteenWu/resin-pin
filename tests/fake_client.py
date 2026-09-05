from __future__ import annotations

from typing import Any


class FakeClient:
    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.platforms: list[dict[str, Any]] = []
        self.created_bodies: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []
        self._seq = 0

    def list_nodes(self, region: str | None = None, enabled: bool | None = True) -> list[dict[str, Any]]:
        out = []
        for node in self.nodes:
            if region and (node.get("region") or "") != region:
                continue
            if enabled is True and not node.get("enabled", True):
                continue
            out.append(node)
        return out

    def list_platforms(self) -> list[dict[str, Any]]:
        return list(self.platforms)

    def create_platform(self, body: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        item = {
            "id": f"p-{self._seq}",
            "name": body["name"],
            "regex_filters": list(body.get("regex_filters") or []),
            "region_filters": list(body.get("region_filters") or []),
            "routable_node_count": 1,
        }
        self.platforms.append(item)
        self.created_bodies.append(body)
        return item

    def update_platform(self, platform_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.updated.append((platform_id, body))
        for item in self.platforms:
            if item["id"] == platform_id:
                item.update(body)
                return item
        raise KeyError(platform_id)

    def delete_platform(self, platform_id: str) -> None:
        self.deleted.append(platform_id)
        self.platforms = [item for item in self.platforms if item["id"] != platform_id]
