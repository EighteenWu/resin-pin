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
            patch_state(path, sync_interval_seconds=3600)
            save_state(path, {"nodes": {"abc": {"name": "hk-1"}}})
            data = load_state(path)
            self.assertEqual(data["sync_interval_seconds"], 3600)
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


if __name__ == "__main__":
    unittest.main()
