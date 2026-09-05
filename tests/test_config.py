from __future__ import annotations

import os
import unittest

from resin_pin.config import Config


class ConfigTests(unittest.TestCase):
    def test_sgp_alias_and_proxy_url(self) -> None:
        keys = {
            "PIN_REGIONS": "tw, jp, hk, sgp",
            "RESIN_PROXY_TOKEN": "proxy-token",
            "RESIN_PUBLIC_HOST": "pin.example.com",
            "RESIN_PUBLIC_PORT": "2260",
            "RESIN_ADMIN_TOKEN": "admin",
            "RESIN_URL": "http://resin",
            "PIN_LISTEN": "0.0.0.0:2270",
            "PIN_STATE_PATH": "./data/state.json",
            "PIN_PULL_TOKEN": "pull-secret",
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
        self.assertEqual(cfg.regions, ("tw", "jp", "hk", "sg"))
        self.assertEqual(cfg.proxy_url("hk-5"), "http://hk-5:proxy-token@pin.example.com:2260")
        self.assertEqual(cfg.pull_token, "pull-secret")


if __name__ == "__main__":
    unittest.main()
