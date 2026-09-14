from __future__ import annotations

import os
import unittest

from resin_pin.config import Config, normalize_max_latency_ms, normalize_regions, normalize_sync_interval


class ConfigTests(unittest.TestCase):
    def test_default_regions_omit_sg(self) -> None:
        keys = (
            "PIN_REGIONS",
            "RESIN_ADMIN_TOKEN",
            "RESIN_PROXY_TOKEN",
            "RESIN_URL",
            "PIN_LISTEN",
        )
        old = {key: os.environ.get(key) for key in keys}
        os.environ.pop("PIN_REGIONS", None)
        os.environ["RESIN_ADMIN_TOKEN"] = "admin"
        try:
            cfg = Config.from_env()
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(cfg.regions, ("tw", "jp", "hk", "kr"))
        self.assertNotIn("sg", cfg.regions)

    def test_sgp_alias_and_proxy_url(self) -> None:
        keys = {
            "PIN_REGIONS": "tw, jp, hk, sgp, korea",
            "RESIN_PROXY_TOKEN": "proxy-token",
            "RESIN_PUBLIC_HOST": "pin.example.com",
            "RESIN_PUBLIC_PORT": "2260",
            "RESIN_ADMIN_TOKEN": "admin",
            "RESIN_URL": "http://resin",
            "PIN_LISTEN": "0.0.0.0:2270",
            "PIN_STATE_PATH": "./data/state.json",
            "PIN_PULL_TOKEN": "pull-secret",
            "PIN_MAX_LATENCY_MS": "180",
        }
        old = {key: os.environ.get(key) for key in keys}
        os.environ.update(keys)
        try:
            cfg = Config.from_env()
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(cfg.regions, ("tw", "jp", "hk", "sg", "kr"))
        self.assertEqual(cfg.proxy_url("hk-5"), "http://hk-5:proxy-token@pin.example.com:2260")
        self.assertEqual(cfg.pull_token, "pull-secret")
        self.assertEqual(cfg.max_latency_ms, 180)

    def test_normalize_sync_interval(self) -> None:
        self.assertEqual(normalize_sync_interval(0), 0)
        self.assertEqual(normalize_sync_interval("3600"), 3600)
        with self.assertRaises(ValueError):
            normalize_sync_interval(30)
        with self.assertRaises(ValueError):
            normalize_sync_interval(-1)
        with self.assertRaises(ValueError):
            normalize_sync_interval(True)

    def test_normalize_regions(self) -> None:
        self.assertEqual(normalize_regions("tw, jp, hk, sgp, korea"), ("tw", "jp", "hk", "sg", "kr"))
        self.assertEqual(normalize_regions(["HK", "sg", "sg"]), ("hk", "sg"))
        with self.assertRaises(ValueError):
            normalize_regions([])
        with self.assertRaises(ValueError):
            normalize_regions("us")
        with self.assertRaises(ValueError):
            normalize_regions(1)

    def test_normalize_max_latency_ms(self) -> None:
        self.assertEqual(normalize_max_latency_ms(0), 0)
        self.assertEqual(normalize_max_latency_ms("180"), 180)
        with self.assertRaises(ValueError):
            normalize_max_latency_ms(-1)
        with self.assertRaises(ValueError):
            normalize_max_latency_ms(True)
        with self.assertRaises(ValueError):
            normalize_max_latency_ms("")


if __name__ == "__main__":
    unittest.main()
