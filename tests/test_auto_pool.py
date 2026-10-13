from __future__ import annotations

import io
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from resin_pin.auto_pool import POOL_LIMIT, POOL_NAME, probe_candidate, refresh_auto_pool
from resin_pin.server import App, make_handler
from resin_pin.state import load_state
from tests.fake_client import FakeClient
from tests.test_reconcile import cfg, healthy


def row(name: str, milliseconds: float = 100, **fields: object) -> dict:
    return {
        "name": name,
        "region": name[:2],
        "node_hash": name,
        "tag": f"provider/{name}.test|node",
        "egress_ip": f"exit-{name}",
        "ready": True,
        "quark_ms": milliseconds,
        "baidu_ms": milliseconds / 2,
        "ok": True,
        **fields,
    }


def passed(item: dict, _config: object) -> dict:
    return dict(item)


class AutoPoolTests(unittest.TestCase):
    def test_ranks_verified_nodes_and_excludes_failed_unready_and_other_regions(self) -> None:
        client = FakeClient()
        rows = [row(f"jp-{i}", i * 10) for i in range(1, 12)]
        rows += [row("hk-1", 5), row("jp-failed", 1, ok=False), row("jp-unready", 1, ready=False), row("tw-1", 1)]
        with patch("resin_pin.auto_pool.probe_candidate", side_effect=passed) as probe:
            result = refresh_auto_pool(client, cfg("unused"), rows)
        selected = result["selected"]
        self.assertEqual(len(selected), POOL_LIMIT)
        self.assertEqual(selected[0]["name"], "hk-1")
        self.assertEqual(selected[-1]["name"], "jp-7")
        self.assertEqual(len(result["rejected"]), 1)
        self.assertEqual(probe.call_count, 13)
        body = client.created_bodies[0]
        self.assertEqual(body["name"], POOL_NAME)
        self.assertEqual(body["allocation_policy"], "PREFER_LOW_LATENCY")
        self.assertEqual(len(body["regex_filters"]), 1)
        regex = re.compile(body["regex_filters"][0])
        for item in selected:
            self.assertIsNotNone(regex.fullmatch(item["tag"]))
        self.assertIsNone(regex.fullmatch(rows[-1]["tag"]))

    def test_deduplicates_nodes_and_exit_ips(self) -> None:
        client = FakeClient()
        rows = [row("jp-1", 10), row("jp-2", 20, egress_ip="exit-jp-1"), row("hk-1", 30, node_hash="jp-1"), row("hk-2", 40)]
        with patch("resin_pin.auto_pool.probe_candidate", side_effect=passed):
            result = refresh_auto_pool(client, cfg("unused"), rows)
        self.assertEqual([x["name"] for x in result["selected"]], ["jp-1", "hk-2"])

    def test_next_refresh_replaces_failed_nodes_and_keeps_manual_platforms(self) -> None:
        client = FakeClient()
        client.platforms = [{"id": "manual", "name": "operator-owned", "regex_filters": ["keep"]}]
        with patch("resin_pin.auto_pool.probe_candidate", side_effect=passed):
            refresh_auto_pool(client, cfg("unused"), [row("jp-1")])
            result = refresh_auto_pool(client, cfg("unused"), [row("jp-1", ok=False), row("hk-2")])
        self.assertEqual(result["selected"][0]["name"], "hk-2")
        self.assertEqual(len(client.updated), 1)
        regex = re.compile(client.updated[0][1]["regex_filters"][0])
        self.assertIsNone(regex.fullmatch(row("jp-1")["tag"]))
        self.assertEqual(client.platforms[0]["regex_filters"], ["keep"])
        self.assertEqual(client.deleted, [])

    def test_all_failed_does_not_open_the_unfiltered_region_pool(self) -> None:
        client = FakeClient()
        with patch("resin_pin.auto_pool.probe_candidate", side_effect=passed):
            refresh_auto_pool(client, cfg("unused"), [row("jp-1")])
            result = refresh_auto_pool(client, cfg("unused"), [row("jp-1", ok=False)])
        self.assertTrue(result["empty"])
        self.assertEqual(result["selected"], [])
        self.assertEqual(client.updated[0][1]["regex_filters"], ["^$"])

    def test_no_candidates_does_not_probe_unready_nodes(self) -> None:
        client = FakeClient()
        with patch("resin_pin.auto_pool.probe_candidate") as probe:
            result = refresh_auto_pool(client, cfg("unused"), [row("hk-1", ready=False)])
        probe.assert_not_called()
        self.assertTrue(result["empty"])

    def test_app_setting_persists_across_pin_reconciliation_and_restart(self) -> None:
        client = FakeClient()
        client.nodes = [healthy("jp", 1)]
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            app = App(cfg(path))
            app.client = client
            self.assertFalse(app.auto_pool_enabled)
            app.set_auto_pool_enabled(True)
            with patch("resin_pin.server.refresh_auto_pool", return_value={"checked": 1, "selected": [], "rejected": [], "empty": True}) as refresh:
                app.run_sync()
            refresh.assert_called_once()
            self.assertTrue(load_state(path)["auto_pool_enabled"])
            self.assertTrue(App(cfg(path)).auto_pool_enabled)
            self.assertTrue(app.catalog()["auto_pool"]["empty"])
            with self.assertRaises(ValueError):
                app.set_auto_pool_enabled("false")


class Response(io.BytesIO):
    status = 200


class AutoPoolHTTPTests(unittest.TestCase):
    def test_setting_requires_authentication_and_preserves_existing_sync_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "state.json")
            app = App(cfg(path))
            app.set_sync_interval(300)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/api/settings"
            try:
                for auth, body, status in ((None, b'{"auto_pool_enabled":true}', 401), ("Bearer admin", b'{"auto_pool_enabled":"true"}', 400)):
                    headers = {"Content-Type": "application/json"}
                    if auth:
                        headers["Authorization"] = auth
                    with self.assertRaises(HTTPError) as failure:
                        urlopen(Request(url, data=body, headers=headers), timeout=2)
                    self.assertEqual(failure.exception.code, status)
                    self.assertFalse(app.auto_pool_enabled)
                request = Request(url, data=b'{"auto_pool_enabled":true}', headers={"Content-Type": "application/json", "Authorization": "Bearer admin"})
                with urlopen(request, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                self.assertTrue(load_state(path)["auto_pool_enabled"])
                self.assertEqual(load_state(path)["sync_interval_seconds"], 300)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class ProbeTests(unittest.TestCase):
    def test_expected_quark_error_is_connectivity_success_and_internal_proxy_is_used(self) -> None:
        def open_response(request, timeout):
            self.assertEqual(timeout, 4.0)
            if request.get_method() == "GET":
                raise HTTPError(request.full_url, 400, "expected", {}, io.BytesIO(b'{"code":14001}'))
            return Response()

        with patch("resin_pin.auto_pool.build_opener") as builder:
            builder.return_value.open.side_effect = open_response
            result = probe_candidate(row("jp-1"), cfg("unused"))
        self.assertTrue(result["ok"])
        self.assertEqual(builder.return_value.open.call_count, 4)
        handler = builder.call_args.args[0]
        self.assertEqual(handler.proxies["https"], "http://jp-1:proxy-token@resin")
        self.assertNotIn("proxy-token", repr(result))

    def test_tls_failure_is_rejected_without_leaking_proxy_password(self) -> None:
        with patch("resin_pin.auto_pool.build_opener") as builder:
            builder.return_value.open.side_effect = URLError("http://user:secret@proxy.invalid")
            result = probe_candidate(row("jp-1"), cfg("unused"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "quark: URLError")
        self.assertNotIn("secret", repr(result))

    def test_wrong_http_status_or_business_payload_is_rejected(self) -> None:
        for body in (b'{"code":0}', b'[]', b'not json'):
            with self.subTest(body=body), patch("resin_pin.auto_pool.build_opener") as builder:
                builder.return_value.open.return_value = Response(body)
                self.assertFalse(probe_candidate(row("jp-1"), cfg("unused"))["ok"])

    def test_baidu_failure_rejects_a_node_that_can_reach_quark(self) -> None:
        responses = [HTTPError("https://quark.test", 400, "expected", {}, io.BytesIO(b'{"code":14001}')), HTTPError("https://baidu.test", 403, "blocked", {}, io.BytesIO())]
        with patch("resin_pin.auto_pool.build_opener") as builder:
            builder.return_value.open.side_effect = responses
            result = probe_candidate(row("jp-1"), cfg("unused"))
        self.assertFalse(result["ok"])
        self.assertIn("baidu: unexpected HTTP 403", result["error"])

    def test_failure_in_the_second_round_does_not_admit_a_node(self) -> None:
        responses = [HTTPError("https://quark.test", 400, "expected", {}, io.BytesIO(b'{"code":14001}')), Response(), URLError("second-round failure")]
        with patch("resin_pin.auto_pool.build_opener") as builder:
            builder.return_value.open.side_effect = responses
            result = probe_candidate(row("jp-1"), cfg("unused"))
            self.assertEqual(builder.return_value.open.call_count, 3)
        self.assertFalse(result["ok"])
