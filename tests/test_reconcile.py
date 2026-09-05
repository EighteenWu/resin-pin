from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from resin_pin.config import MANAGED_MARKER, Config
from resin_pin.reconcile import (
    allocate_name,
    catalog_rows,
    is_eligible,
    is_managed_platform,
    node_status,
    pin_regex,
    quote_re2,
    reconcile,
)
from tests.fake_client import FakeClient


def cfg(state_path: str) -> Config:
    return Config(
        resin_url="http://resin",
        admin_token="admin",
        proxy_token="proxy-token",
        public_host="pin.example.com",
        public_port=2260,
        listen_host="0.0.0.0",
        listen_port=2270,
        state_path=state_path,
        sync_interval_seconds=86400,
        sync_on_start=True,
        ui_token="admin",
        regions=("tw", "jp", "hk", "sg", "kr"),
    )


def healthy(region: str, idx: int, **overrides: object) -> dict:
    node = {
        "node_hash": f"{region}{idx:02d}" + "ab" * 14,
        "enabled": True,
        "has_outbound": True,
        "circuit_open_since": None,
        "egress_ip": f"1.2.3.{idx}",
        "region": region,
        "display_tag": f"{region.upper()}-{idx}",
        "reference_latency_ms": 80.0,
        "tags": [{"tag": f"pool/{region}-{idx}", "subscription_name": "pool"}],
    }
    node.update(overrides)
    return node


class FilterTests(unittest.TestCase):
    def test_quote_and_pin_regex_is_exact_must_rule(self) -> None:
        self.assertEqual(quote_re2("pool/hk-5.vip"), r"pool/hk-5\.vip")
        self.assertEqual(pin_regex("pool/hk-5"), r"^pool/hk-5$")

    def test_managed_marker_detects_our_platforms_only(self) -> None:
        ours = {"name": "hk-1", "regex_filters": [r"^pool/hk-1$"]}
        marked = {"name": "custom", "regex_filters": [r"^pool/hk-1$", MANAGED_MARKER]}
        manual = {"regex_filters": ["香港"], "name": "hk-1"}
        self.assertTrue(is_managed_platform(ours))
        self.assertTrue(is_managed_platform(marked))
        self.assertFalse(is_managed_platform(manual))

    def test_eligible_requires_region_health_egress_and_tag(self) -> None:
        regions = ("tw", "jp", "hk", "sg", "kr")
        self.assertTrue(is_eligible(healthy("hk", 1), regions))
        self.assertTrue(is_eligible(healthy("kr", 1), regions))
        self.assertFalse(is_eligible(healthy("us", 1), regions))
        self.assertFalse(is_eligible(healthy("hk", 1, circuit_open_since="2026-01-01T00:00:00Z"), regions))
        self.assertFalse(is_eligible(healthy("hk", 1, egress_ip=""), regions))
        self.assertFalse(is_eligible(healthy("hk", 1, has_outbound=False), regions))
        self.assertFalse(is_eligible(healthy("hk", 1, tags=[], display_tag="HK-1"), regions))

    def test_allocate_name_fills_gaps(self) -> None:
        self.assertEqual(allocate_name("hk", {"hk-1", "hk-3"}), "hk-2")

    def test_status_codes(self) -> None:
        self.assertEqual(node_status(None, 0), "gone")
        self.assertEqual(node_status(healthy("hk", 1, circuit_open_since="t"), 1), "circuit")
        self.assertEqual(node_status(healthy("hk", 1, egress_ip=""), 1), "no_egress")
        self.assertEqual(node_status(healthy("hk", 1), 0), "not_routable")
        self.assertEqual(node_status(healthy("hk", 1), 1), "ready")


class ReconcileTests(unittest.TestCase):
    def test_creates_one_platform_per_healthy_node_and_stable_names(self) -> None:
        client = FakeClient()
        client.nodes = [healthy("hk", 5), healthy("jp", 1), healthy("sg", 2), healthy("kr", 1)]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            first = reconcile(client, cfg(path), path)
            self.assertEqual(sorted(first.created), ["hk-1", "jp-1", "kr-1", "sg-1"])
            self.assertEqual(len(client.platforms), 4)
            self.assertTrue(all(item["regex_filters"][0].startswith("^") for item in client.platforms))

            client.nodes.append(healthy("hk", 6))
            second = reconcile(client, cfg(path), path)
            self.assertEqual(second.created, ["hk-2"])
            names = {item["name"] for item in client.platforms}
            self.assertEqual(names, {"hk-1", "hk-2", "jp-1", "kr-1", "sg-1"})

            hk1 = next(item for item in client.platforms if item["name"] == "hk-1")
            self.assertEqual(hk1["regex_filters"][0], r"^pool/hk-5$")
            self.assertEqual(hk1["region_filters"], ["hk"])

    def test_does_not_steal_manual_platform_names(self) -> None:
        client = FakeClient()
        client.platforms = [
            {
                "id": "manual",
                "name": "hk-1",
                "regex_filters": ["香港"],
                "region_filters": ["hk"],
                "routable_node_count": 8,
            }
        ]
        client.nodes = [healthy("hk", 1)]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            result = reconcile(client, cfg(path), path)
            self.assertEqual(result.created, ["hk-2"])
            self.assertEqual({item["name"] for item in client.platforms}, {"hk-1", "hk-2"})

    def test_deletes_managed_platform_when_node_leaves(self) -> None:
        client = FakeClient()
        client.nodes = [healthy("tw", 1), healthy("tw", 2)]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            reconcile(client, cfg(path), path)
            client.nodes = [healthy("tw", 2)]
            result = reconcile(client, cfg(path), path)
            self.assertEqual(result.deleted, ["tw-1"])
            self.assertEqual([item["name"] for item in client.platforms], ["tw-2"])

    def test_updates_pin_when_tag_changes(self) -> None:
        client = FakeClient()
        node = healthy("jp", 1)
        client.nodes = [node]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            reconcile(client, cfg(path), path)
            node["tags"] = [{"tag": "pool/jp-1-renamed"}]
            result = reconcile(client, cfg(path), path)
            self.assertEqual(result.updated, ["jp-1"])
            self.assertEqual(client.platforms[0]["regex_filters"][0], r"^pool/jp-1-renamed$")

    def test_catalog_builds_import_urls_and_live_status(self) -> None:
        client = FakeClient()
        ready = healthy("hk", 5)
        broken = healthy("hk", 6, circuit_open_since="2026-09-05T00:00:00Z")
        client.nodes = [ready, broken]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            reconcile(client, cfg(path), path)
            # second node was ineligible, only hk-1 exists
            rows = catalog_rows(client, cfg(path), path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["proxy_url"], "http://hk-1:proxy-token@pin.example.com:2260")
            self.assertTrue(rows[0]["ready"])
            self.assertEqual(rows[0]["status_label"], "可用")

            client.nodes[0]["circuit_open_since"] = "2026-09-05T00:00:00Z"
            rows = catalog_rows(client, cfg(path), path)
            self.assertEqual(rows[0]["status_label"], "熔断")
            self.assertFalse(rows[0]["ready"])


if __name__ == "__main__":
    unittest.main()
