from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from resin_pin.reconcile import reconcile
from resin_pin.server import App
from resin_pin.state import load_state, patch_state, save_state
from tests.fake_client import FakeClient
from tests.test_reconcile import cfg, healthy


class SettingsTests(unittest.TestCase):
    def test_save_state_keeps_interval_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            patch_state(path, sync_interval_seconds=3600, max_latency_ms=70)
            save_state(path, {"nodes": {"abc": {"name": "hk-1"}}})
            data = load_state(path)
            self.assertEqual(data["sync_interval_seconds"], 3600)
            self.assertEqual(data["max_latency_ms"], 70)
            self.assertEqual(data["nodes"]["abc"]["name"], "hk-1")

    def test_app_reads_and_updates_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            patch_state(path, sync_interval_seconds=1800)
            app = App(cfg(path))
            self.assertEqual(app.sync_interval_seconds, 1800)
            self.assertEqual(app.set_sync_interval(3600), 3600)
            self.assertEqual(app.sync_interval_seconds, 3600)
            self.assertEqual(load_state(path)["sync_interval_seconds"], 3600)
            self.assertIsNotNone(app.next_sync_at())
            self.assertTrue(app.sync_wake.is_set())

    def test_app_falls_back_to_env_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            app = App(cfg(path))
            self.assertEqual(app.sync_interval_seconds, 86400)

    def test_reconcile_does_not_clobber_interval(self) -> None:
        client = FakeClient()
        client.nodes = [healthy("hk", 1)]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            patch_state(path, sync_interval_seconds=3600)
            reconcile(client, cfg(path), path)
            self.assertEqual(load_state(path)["sync_interval_seconds"], 3600)
            self.assertIn("nodes", load_state(path))

    def test_app_reads_and_updates_max_latency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            patch_state(path, max_latency_ms=120)
            app = App(cfg(path))
            self.assertEqual(app.max_latency_ms, 120)
            self.assertEqual(app.set_max_latency_ms(80), 80)
            self.assertEqual(app.max_latency_ms, 80)
            self.assertEqual(load_state(path)["max_latency_ms"], 80)

    def test_app_falls_back_to_env_max_latency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            app = App(cfg(path))
            self.assertEqual(app.max_latency_ms, 0)

    def test_reconcile_does_not_clobber_max_latency(self) -> None:
        client = FakeClient()
        client.nodes = [healthy("hk", 1)]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            patch_state(path, max_latency_ms=90)
            reconcile(client, cfg(path), path)
            self.assertEqual(load_state(path)["max_latency_ms"], 90)
            self.assertIn("nodes", load_state(path))

    def test_save_state_keeps_regions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            patch_state(path, regions=["tw", "hk"])
            save_state(path, {"nodes": {"abc": {"name": "hk-1"}}})
            data = load_state(path)
            self.assertEqual(data["regions"], ["tw", "hk"])
            self.assertEqual(data["nodes"]["abc"]["name"], "hk-1")

    def test_app_reads_and_updates_regions(self) -> None:
        client = FakeClient()
        client.nodes = [healthy("hk", 1), healthy("sg", 1)]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            app = App(cfg(path, regions=("tw", "jp", "hk", "sg", "kr")))
            app.client = client
            self.assertEqual(app.regions, ("tw", "jp", "hk", "sg", "kr"))
            app.set_regions(["hk", "jp"])
            self.assertEqual(app.regions, ("hk", "jp"))
            self.assertEqual(load_state(path)["regions"], ["hk", "jp"])
            self.assertEqual(app.catalog()["regions"], ["hk", "jp"])
            self.assertIn("sg", app.catalog()["available_regions"])
            app.run_sync()
            self.assertEqual({item["name"] for item in client.platforms}, {"hk-1"})

    def test_catalog_and_export_honor_max_latency(self) -> None:
        client = FakeClient()
        client.nodes = [healthy("hk", 1, reference_latency_ms=80.0)]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            reconcile(client, cfg(path), path)
            app = App(cfg(path))
            app.client = client
            app.set_max_latency_ms(50)
            catalog = app.catalog()
            self.assertEqual(catalog["max_latency_ms"], 50)
            self.assertEqual(catalog["ready_count"], 0)
            self.assertEqual(catalog["rows"][0]["status"], "slow")
            self.assertEqual(app.export_items(), [])
            app.set_max_latency_ms(200)
            catalog = app.catalog()
            self.assertEqual(catalog["ready_count"], 1)
            self.assertEqual(len(app.export_items()), 1)


if __name__ == "__main__":
    unittest.main()
