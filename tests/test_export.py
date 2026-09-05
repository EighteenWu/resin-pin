from __future__ import annotations

import unittest

from resin_pin.export import export_json, export_text, ready_items


class ExportTests(unittest.TestCase):
    def test_ready_items_skip_unready_and_duplicates(self) -> None:
        items = ready_items(
            [
                {"ready": True, "proxy_url": "http://hk-1:x@h:2260", "region": "hk", "name": "hk-1"},
                {"ready": False, "proxy_url": "http://jp-1:x@h:2260", "region": "jp", "name": "jp-1"},
                {"ready": True, "proxy_url": "http://hk-1:x@h:2260", "region": "hk", "name": "hk-1"},
            ]
        )
        self.assertEqual(items, [{"proxyUrl": "http://hk-1:x@h:2260", "region": "hk", "name": "hk-1"}])
        self.assertEqual(export_text(items), "http://hk-1:x@h:2260")
        payload = export_json(items)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["copy_all"], "http://hk-1:x@h:2260")


if __name__ == "__main__":
    unittest.main()
