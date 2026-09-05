from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import MANAGED_MARKER, NAME_PATTERN, Config
from .state import load_state, save_state

_RE2_SPECIAL = re.compile(r"([\\.+*?()|\[\]{}^$])")
_NAME_RE = re.compile(NAME_PATTERN)


class PlatformAPI(Protocol):
    def list_nodes(self, region: str | None = None, enabled: bool | None = True) -> list[dict[str, Any]]: ...

    def list_platforms(self) -> list[dict[str, Any]]: ...

    def create_platform(self, body: dict[str, Any]) -> dict[str, Any]: ...

    def update_platform(self, platform_id: str, body: dict[str, Any]) -> dict[str, Any]: ...

    def delete_platform(self, platform_id: str) -> None: ...


def quote_re2(value: str) -> str:
    return _RE2_SPECIAL.sub(r"\\\1", value)


def pin_regex(tag: str) -> str:
    # Plain exact rule so older Resin builds (no `*` MUST prefix) still compile.
    return "^" + quote_re2(tag) + "$"


def managed_filters(tag: str) -> list[str]:
    return [pin_regex(tag)]


def is_managed_platform(platform: dict[str, Any]) -> bool:
    filters = platform.get("regex_filters") or []
    if MANAGED_MARKER in filters:
        return True
    name = platform.get("name") or ""
    if not _NAME_RE.match(name):
        return False
    return bool(filters) and str(filters[0]).startswith("^") and str(filters[0]).endswith("$")


def node_tag(node: dict[str, Any]) -> str:
    tags = node.get("tags") or []
    candidates = [item.get("tag") or "" for item in tags if item.get("tag")]
    if node.get("display_tag"):
        display = str(node["display_tag"])
        if "/" in display:
            candidates.append(display)
    if not candidates:
        return ""
    return max(candidates, key=len)


def is_eligible(node: dict[str, Any], regions: tuple[str, ...]) -> bool:
    region = (node.get("region") or "").lower()
    if region not in regions:
        return False
    if not node.get("enabled", True):
        return False
    if not node.get("has_outbound"):
        return False
    if node.get("circuit_open_since"):
        return False
    if not node.get("egress_ip"):
        return False
    if not node_tag(node):
        return False
    return True


def node_status(node: dict[str, Any] | None, routable_count: int) -> str:
    if node is None:
        return "gone"
    if not node.get("enabled", True):
        return "disabled"
    if node.get("circuit_open_since"):
        return "circuit"
    if not node.get("has_outbound"):
        return "no_outbound"
    if not node.get("egress_ip"):
        return "no_egress"
    if routable_count <= 0:
        return "not_routable"
    return "ready"


def status_label(code: str) -> str:
    return {
        "ready": "可用",
        "circuit": "熔断",
        "no_egress": "无出口",
        "no_outbound": "无出站",
        "not_routable": "未就绪",
        "disabled": "已停用",
        "gone": "已失效",
    }.get(code, code)


def allocate_name(region: str, used: set[str]) -> str:
    n = 1
    while True:
        name = f"{region}-{n}"
        if name not in used:
            return name
        n += 1


@dataclass
class SyncResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    eligible: int = 0
    error: str = ""


def collect_eligible_nodes(client: PlatformAPI, regions: tuple[str, ...]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for region in regions:
        for node in client.list_nodes(region=region, enabled=True):
            if not is_eligible(node, regions):
                continue
            node_hash = node.get("node_hash") or ""
            if not node_hash:
                continue
            found[node_hash] = node
    return [found[key] for key in sorted(found)]


def _filters_match(platform: dict[str, Any], tag: str, region: str) -> bool:
    expected = managed_filters(tag)
    actual = platform.get("regex_filters") or []
    regions = platform.get("region_filters") or []
    return actual == expected and regions == [region]


def reconcile(client: PlatformAPI, cfg: Config, state_path: str | None = None) -> SyncResult:
    path = state_path or cfg.state_path
    result = SyncResult()
    platforms = client.list_platforms()
    owned = [item for item in platforms if is_managed_platform(item)]
    owned_by_id = {item["id"]: item for item in owned if item.get("id")}
    used_names = {item.get("name") for item in platforms if item.get("name")}
    used_names.discard(None)

    state = load_state(path)
    nodes_state: dict[str, Any] = state.setdefault("nodes", {})

    eligible = collect_eligible_nodes(client, cfg.regions)
    result.eligible = len(eligible)
    eligible_by_hash = {item["node_hash"]: item for item in eligible}

    keep_hashes: set[str] = set()
    for node in eligible:
        node_hash = node["node_hash"]
        region = node["region"].lower()
        tag = node_tag(node)
        record = nodes_state.get(node_hash) or {}
        platform_id = record.get("platform_id")
        platform = owned_by_id.get(platform_id) if platform_id else None
        if platform is None:
            platform = next(
                (
                    item
                    for item in owned
                    if _filters_match(item, tag, region)
                    or (record.get("name") and item.get("name") == record.get("name"))
                ),
                None,
            )
        if platform is None:
            name = record.get("name")
            if not name or name in used_names:
                name = allocate_name(region, used_names)
            created = client.create_platform(
                {
                    "name": name,
                    "regex_filters": managed_filters(tag),
                    "region_filters": [region],
                }
            )
            platform = created
            used_names.add(name)
            owned.append(platform)
            if platform.get("id"):
                owned_by_id[platform["id"]] = platform
            result.created.append(name)
        elif not _filters_match(platform, tag, region):
            client.update_platform(
                platform["id"],
                {
                    "regex_filters": managed_filters(tag),
                    "region_filters": [region],
                },
            )
            result.updated.append(platform["name"])
        nodes_state[node_hash] = {
            "name": platform["name"],
            "platform_id": platform["id"],
            "region": region,
            "tag": tag,
        }
        keep_hashes.add(node_hash)

    keep_ids = {nodes_state[key]["platform_id"] for key in keep_hashes}
    for node_hash in [key for key in list(nodes_state) if key not in keep_hashes]:
        nodes_state.pop(node_hash, None)

    deleted_ids: set[str] = set()
    for platform in list(owned):
        platform_id = platform.get("id")
        if not platform_id or platform_id in keep_ids or platform_id in deleted_ids:
            continue
        client.delete_platform(platform_id)
        deleted_ids.add(platform_id)
        result.deleted.append(platform.get("name") or platform_id)

    save_state(path, state)
    return result


def catalog_rows(
    client: PlatformAPI,
    cfg: Config,
    state_path: str | None = None,
) -> list[dict[str, Any]]:
    path = state_path or cfg.state_path
    state = load_state(path)
    platforms = {item["id"]: item for item in client.list_platforms() if item.get("id")}
    nodes: dict[str, dict[str, Any]] = {}
    for region in cfg.regions:
        for node in client.list_nodes(region=region, enabled=None):
            if node.get("node_hash"):
                nodes[node["node_hash"]] = node

    rows: list[dict[str, Any]] = []
    for node_hash, record in (state.get("nodes") or {}).items():
        platform = platforms.get(record.get("platform_id") or "")
        node = nodes.get(node_hash)
        name = (platform or {}).get("name") or record.get("name") or ""
        routable = int((platform or {}).get("routable_node_count") or 0)
        code = node_status(node, routable)
        region = (record.get("region") or (node or {}).get("region") or "").lower()
        rows.append(
            {
                "name": name,
                "region": region,
                "node_hash": node_hash,
                "tag": record.get("tag") or node_tag(node or {}),
                "egress_ip": (node or {}).get("egress_ip") or "",
                "latency_ms": (node or {}).get("reference_latency_ms"),
                "routable_node_count": routable,
                "status": code,
                "status_label": status_label(code),
                "proxy_url": cfg.proxy_url(name) if name else "",
                "ready": code == "ready",
            }
        )
    region_rank = {item: idx for idx, item in enumerate(cfg.regions)}
    rows.sort(
        key=lambda row: (
            region_rank.get(row["region"], 99),
            _name_sort_key(row["name"]),
        )
    )
    return rows


def _name_sort_key(name: str) -> tuple[str, int]:
    match = _NAME_RE.match(name)
    if not match:
        return (name, 0)
    return (match.group(1), int(match.group(2)))
