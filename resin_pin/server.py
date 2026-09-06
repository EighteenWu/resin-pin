from __future__ import annotations

import hmac
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .client import ResinClient, ResinError
from .config import Config, normalize_sync_interval
from .export import export_json, export_text, ready_items
from .reconcile import SyncResult, catalog_rows, reconcile, status_label
from .state import load_state, patch_state

STATIC_DIR = Path(__file__).parent / "static"


@dataclass
class SyncSnapshot:
    at: str = ""
    result: dict[str, Any] | None = None
    error: str = ""


class App:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.client = ResinClient(cfg.resin_url, cfg.admin_token)
        self.lock = threading.Lock()
        self.syncing = False
        self.last = SyncSnapshot()
        self.sync_wake = threading.Event()
        self.sync_interval_seconds = _load_sync_interval(cfg)
        self.wait_started_at: datetime | None = None

    def run_sync(self) -> SyncResult:
        with self.lock:
            if self.syncing:
                raise RuntimeError("sync already running")
            self.syncing = True
        try:
            result = reconcile(self.client, self.cfg)
            self.last = SyncSnapshot(
                at=datetime.now(timezone.utc).isoformat(),
                result=asdict(result),
            )
            return result
        except Exception as exc:
            self.last = SyncSnapshot(
                at=datetime.now(timezone.utc).isoformat(),
                error=str(exc),
            )
            raise
        finally:
            self.syncing = False

    def catalog(self) -> dict[str, Any]:
        rows = catalog_rows(self.client, self.cfg)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        ready_urls = [row["proxy_url"] for row in rows if row["ready"] and row["proxy_url"]]
        return {
            "rows": rows,
            "counts": counts,
            "ready_count": len(ready_urls),
            "total": len(rows),
            "copy_all": "\n".join(ready_urls),
            "public_host": f"{self.cfg.public_host}:{self.cfg.public_port}",
            "regions": list(self.cfg.regions),
            "syncing": self.syncing,
            "last_sync": asdict(self.last),
            "sync_interval_seconds": self.sync_interval_seconds,
            "next_sync_at": self.next_sync_at(),
            "status_labels": {code: status_label(code) for code in counts},
        }

    def next_sync_at(self) -> str | None:
        if self.sync_interval_seconds <= 0 or self.wait_started_at is None:
            return None
        return (self.wait_started_at + timedelta(seconds=self.sync_interval_seconds)).isoformat()

    def set_sync_interval(self, value: object) -> int:
        seconds = normalize_sync_interval(value)
        patch_state(self.cfg.state_path, sync_interval_seconds=seconds)
        self.sync_interval_seconds = seconds
        self.wait_started_at = datetime.now(timezone.utc)
        self.sync_wake.set()
        return seconds

    def export_items(self) -> list[dict[str, str]]:
        return ready_items(catalog_rows(self.client, self.cfg))


def _unauthorized(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(401)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("WWW-Authenticate", "Bearer")
    handler.end_headers()
    handler.wfile.write(b'{"error":{"code":"UNAUTHORIZED","message":"invalid token"}}')


def _token_matches(given: str, expected: str) -> bool:
    if not expected:
        return True
    left = given.encode("utf-8")
    right = expected.encode("utf-8")
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    ui_token = app.cfg.ui_token
    pull_token = app.cfg.pull_token

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def _ok(self, body: bytes, content_type: str, cache: str = "no-store") -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _bearer(self) -> str:
            header = self.headers.get("Authorization") or ""
            if header.startswith("Bearer "):
                return header[7:]
            return ""

        def _query_token(self) -> str:
            return (parse_qs(urlparse(self.path).query).get("token") or [""])[0]

        def _authorized(self) -> bool:
            return _token_matches(self._bearer(), ui_token)

        def _pull_authorized(self) -> bool:
            return _token_matches(self._query_token() or self._bearer(), pull_token)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path in {"/", "/index.html"}:
                html = (STATIC_DIR / "index.html").read_bytes()
                self._ok(html, "text/html; charset=utf-8")
                return
            if path == "/healthz":
                self._json({"ok": True, "syncing": app.syncing})
                return
            if path in {"/api/export", "/api/export.json"}:
                if not self._pull_authorized():
                    _unauthorized(self)
                    return
                try:
                    items = app.export_items()
                except ResinError as exc:
                    self._json({"error": {"code": "RESIN", "message": exc.message}}, 502)
                    return
                except Exception as exc:
                    self._json({"error": {"code": "INTERNAL", "message": str(exc)}}, 500)
                    return
                query = parse_qs(parsed.query)
                want_json = path.endswith(".json") or (query.get("format") or [""])[0].lower() == "json"
                if want_json:
                    self._json(export_json(items))
                    return
                body = (export_text(items) + ("\n" if items else "")).encode("utf-8")
                self._ok(body, "text/plain; charset=utf-8")
                return
            if path == "/api/catalog":
                if not self._authorized():
                    _unauthorized(self)
                    return
                try:
                    self._json(app.catalog())
                except ResinError as exc:
                    self._json({"error": {"code": "RESIN", "message": exc.message}}, 502)
                except Exception as exc:
                    self._json({"error": {"code": "INTERNAL", "message": str(exc)}}, 500)
                return
            self.send_error(404)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON object required")
            return data

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path not in {"/api/sync", "/api/settings"}:
                self.send_error(404)
                return
            if not self._authorized():
                _unauthorized(self)
                return
            if path == "/api/settings":
                try:
                    payload = self._read_json()
                    seconds = app.set_sync_interval(payload.get("sync_interval_seconds"))
                except json.JSONDecodeError:
                    self._json({"error": {"code": "BAD_REQUEST", "message": "invalid JSON"}}, 400)
                    return
                except ValueError as exc:
                    self._json({"error": {"code": "BAD_REQUEST", "message": str(exc)}}, 400)
                    return
                self._json(
                    {
                        "ok": True,
                        "sync_interval_seconds": seconds,
                        "next_sync_at": app.next_sync_at(),
                    }
                )
                return
            try:
                result = app.run_sync()
                self._json({"ok": True, "result": asdict(result), "catalog": app.catalog()})
            except RuntimeError as exc:
                self._json({"error": {"code": "BUSY", "message": str(exc)}}, 409)
            except ResinError as exc:
                self._json({"error": {"code": "RESIN", "message": exc.message}}, 502)
            except Exception as exc:
                self._json({"error": {"code": "INTERNAL", "message": str(exc)}}, 500)

    return Handler


def serve(cfg: Config) -> None:
    app = App(cfg)
    if cfg.sync_on_start:
        threading.Thread(target=_safe_sync, args=(app,), name="pin-sync-start", daemon=True).start()
    threading.Thread(target=_loop_sync, args=(app,), name="pin-sync-loop", daemon=True).start()
    server = ThreadingHTTPServer((cfg.listen_host, cfg.listen_port), make_handler(app))
    print(f"resin-pin listening on http://{cfg.listen_host}:{cfg.listen_port}", flush=True)
    server.serve_forever()


def _load_sync_interval(cfg: Config) -> int:
    raw = load_state(cfg.state_path).get("sync_interval_seconds")
    if raw is None:
        return cfg.sync_interval_seconds
    try:
        return normalize_sync_interval(raw)
    except ValueError:
        return cfg.sync_interval_seconds


def _safe_sync(app: App) -> None:
    try:
        app.run_sync()
    except Exception as exc:
        print(f"startup sync failed: {exc}", flush=True)


def _loop_sync(app: App) -> None:
    while True:
        interval = app.sync_interval_seconds
        app.wait_started_at = datetime.now(timezone.utc)
        if interval <= 0:
            app.sync_wake.wait()
            app.sync_wake.clear()
            continue
        woken = app.sync_wake.wait(interval)
        app.sync_wake.clear()
        if woken:
            continue
        try:
            app.run_sync()
        except RuntimeError:
            continue
        except Exception as exc:
            print(f"interval sync failed: {exc}", flush=True)
