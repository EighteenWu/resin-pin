from __future__ import annotations

import hmac
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .client import ResinClient, ResinError
from .config import Config
from .reconcile import SyncResult, catalog_rows, reconcile, status_label

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
            "status_labels": {code: status_label(code) for code in counts},
        }


def _unauthorized(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(401)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("WWW-Authenticate", "Bearer")
    handler.end_headers()
    handler.wfile.write(b'{"error":{"code":"UNAUTHORIZED","message":"invalid token"}}')


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    token = app.cfg.ui_token

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

        def _authorized(self) -> bool:
            if not token:
                return True
            header = self.headers.get("Authorization") or ""
            if not header.startswith("Bearer "):
                return False
            given = header[7:].encode("utf-8")
            expected = token.encode("utf-8")
            if len(given) != len(expected):
                return False
            return hmac.compare_digest(given, expected)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                html = (STATIC_DIR / "index.html").read_bytes()
                self._ok(html, "text/html; charset=utf-8")
                return
            if path == "/healthz":
                self._json({"ok": True, "syncing": app.syncing})
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

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/api/sync":
                self.send_error(404)
                return
            if not self._authorized():
                _unauthorized(self)
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
    if cfg.sync_interval_seconds > 0:
        threading.Thread(target=_loop_sync, args=(app, cfg.sync_interval_seconds), name="pin-sync-loop", daemon=True).start()
    server = ThreadingHTTPServer((cfg.listen_host, cfg.listen_port), make_handler(app))
    print(f"resin-pin listening on http://{cfg.listen_host}:{cfg.listen_port}", flush=True)
    server.serve_forever()


def _safe_sync(app: App) -> None:
    try:
        app.run_sync()
    except Exception as exc:
        print(f"startup sync failed: {exc}", flush=True)


def _loop_sync(app: App, interval: int) -> None:
    while True:
        threading.Event().wait(interval)
        try:
            app.run_sync()
        except RuntimeError:
            continue
        except Exception as exc:
            print(f"interval sync failed: {exc}", flush=True)
