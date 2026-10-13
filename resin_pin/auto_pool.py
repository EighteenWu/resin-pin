from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from .config import Config
from .reconcile import PlatformAPI, quote_re2

POOL_NAME = "panso-auto-hkjp"
POOL_REGIONS = ("hk", "jp")
POOL_LIMIT = 8
PROBE_TIMEOUT = 4.0
PROBE_ROUNDS = 2
QUARK_URL = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail?pr=ucpro&fr=pc"
BAIDU_URL = "https://pan.baidu.com/"


def probe_candidate(row: dict[str, Any], cfg: Config) -> dict[str, Any]:
    base = urlsplit(cfg.resin_url)
    authority = f"{quote(row['name'], safe='')}:{quote(cfg.proxy_token, safe='')}@{base.netloc}"
    proxy = urlunsplit((base.scheme, authority, "", "", ""))
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
    timings: dict[str, list[float]] = {"quark": [], "baidu": []}
    result = {key: row[key] for key in ("name", "region", "node_hash", "tag", "egress_ip")}
    for _ in range(PROBE_ROUNDS):
        for target, url, method in (("quark", QUARK_URL, "GET"), ("baidu", BAIDU_URL, "HEAD")):
            start = time.monotonic()
            try:
                try:
                    response = opener.open(Request(url, method=method), timeout=PROBE_TIMEOUT)
                except HTTPError as exc:
                    response = exc
                with response:
                    if target == "quark" and response.status == 400:
                        payload = json.loads(response.read(65536))
                        valid = isinstance(payload, dict) and payload.get("code") == 14001
                    else:
                        valid = target == "baidu" and response.status == 200
                if not valid:
                    return {**result, "ok": False, "error": f"{target}: unexpected HTTP {response.status} or business code"}
            except (OSError, HTTPException, json.JSONDecodeError, UnicodeDecodeError) as exc:
                # Network errors may contain authenticated proxy URLs; keep only the error type.
                return {**result, "ok": False, "error": f"{target}: {type(exc).__name__}"}
            timings[target].append((time.monotonic() - start) * 1000)
    return {
        **result,
        "ok": True,
        "quark_ms": round(max(timings["quark"]), 1),
        "baidu_ms": round(max(timings["baidu"]), 1),
    }


def refresh_auto_pool(client: PlatformAPI, cfg: Config, rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [row for row in rows if row["region"] in POOL_REGIONS and row["ready"]]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda row: probe_candidate(row, cfg), candidates))
    good = sorted(
        (result for result in results if result["ok"]),
        key=lambda item: (max(item["quark_ms"], item["baidu_ms"]), item["name"]),
    )
    selected: list[dict[str, Any]] = []
    seen_exits: set[str] = set()
    seen_nodes: set[str] = set()
    for item in good:
        if item["egress_ip"] in seen_exits or item["node_hash"] in seen_nodes:
            continue
        selected.append(item)
        seen_exits.add(item["egress_ip"])
        seen_nodes.add(item["node_hash"])
        if len(selected) == POOL_LIMIT:
            break
    # Resin v1.2 combines multiple filters with AND; one alternation selects all winners.
    regex = "^(" + "|".join(quote_re2(item["tag"]) for item in selected) + ")$" if selected else "^$"
    body = {
        "name": POOL_NAME,
        "region_filters": list(POOL_REGIONS),
        "regex_filters": [regex],
        "allocation_policy": "PREFER_LOW_LATENCY",
        "sticky_ttl": "30m",
        "passive_circuit_breaker_disabled": False,
    }
    platform = next((item for item in client.list_platforms() if item["name"] == POOL_NAME), None)
    if platform is None:
        client.create_platform(body)
    elif any(platform.get(key) != body[key] for key in ("region_filters", "regex_filters", "allocation_policy", "passive_circuit_breaker_disabled")):
        client.update_platform(platform["id"], body)
    return {
        "platform": POOL_NAME,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checked": len(results),
        "selected": selected,
        "rejected": [item for item in results if not item["ok"]],
        "empty": not selected,
    }
