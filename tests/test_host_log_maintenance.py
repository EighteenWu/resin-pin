import datetime as dt
from contextlib import closing
import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import subprocess

SPEC = importlib.util.spec_from_file_location(
    "host_log_maintenance", Path(__file__).parents[1] / "deploy/host_log_maintenance.py")
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)


class HostLogMaintenanceTests(unittest.TestCase):
    def make_db(self, directory, name, timestamp, modified):
        path = directory / name
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE request_logs (ts_ns INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO request_logs VALUES (?)", (int(timestamp * 1e9),))
            db.commit()
        os.utime(path, (modified, modified))
        return path

    def test_retention_preserves_fourteen_calendar_days(self):
        now = dt.datetime(2026, 10, 14, 14, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        cutoff = dt.datetime.fromtimestamp(maintenance.retention_cutoff(now), now.tzinfo)
        self.assertEqual(cutoff, dt.datetime(2026, 10, 1, tzinfo=now.tzinfo))

    def test_only_old_closed_databases_and_orphan_sidecars_are_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            old = self.make_db(root, "request_logs-100.db", 100, 100)
            self.make_db(root, "request_logs-200.db", 100, 100)  # open reader
            self.make_db(root, "request_logs-300.db", 100, 100)  # active, even if old
            orphan = root / "request_logs-50.db-wal"
            orphan.write_bytes(b"old sidecar")
            os.utime(orphan, (100, 100))
            protected = root / "metrics.db"
            protected.write_bytes(b"monitoring")
            self.assertEqual(maintenance.old_resin_logs(root, 150, {"request_logs-200.db"}),
                             sorted([old, orphan]))
            self.assertEqual(protected.read_bytes(), b"monitoring")

    def test_recent_record_or_uncheckpointed_wal_prevents_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            self.make_db(root, "request_logs-100.db", 200, 100)
            self.make_db(root, "request_logs-200.db", 100, 100)
            (root / "request_logs-200.db-wal").write_bytes(b"pending")
            self.make_db(root, "request_logs-300.db", 100, 100)
            self.assertEqual(maintenance.old_resin_logs(root, 150, set()), [])

    def test_newer_mtime_protects_closed_database(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            self.make_db(root, "request_logs-100.db", 100, 200)
            self.make_db(root, "request_logs-200.db", 100, 100)
            self.assertEqual(maintenance.old_resin_logs(root, 150, set()), [])

    def test_rotation_reopens_pm2_logs_without_restarting_workers(self):
        policy = maintenance.worker_policy("pan_so_go_worker", 123)
        self.assertIn("/proc/123/root/root/.pm2/logs/*.log", policy)
        self.assertIn("pm2 reloadLogs", policy)
        self.assertNotIn("copytruncate", policy)
        self.assertNotIn("restart", policy)
        self.assertIn("maxsize 20M", policy)
        self.assertIn("rotate 14", policy)
        self.assertIn("maxage 14", policy)
        self.assertIn("compress", policy)
        with self.assertRaises(ValueError):
            maintenance.worker_policy("other-container", 123)

    def test_zero_exit_with_ignored_logrotate_config_is_failure(self):
        ignored = subprocess.CompletedProcess([], 0, "", "error: Ignoring policy\nHandling 0 logs")
        with patch.object(maintenance, "command", return_value="123"), \
                patch.object(os, "memfd_create", return_value=99, create=True), \
                patch.object(os, "fchmod", create=True), patch.object(os, "write"), \
                patch.object(os, "close"), patch.object(subprocess, "run", return_value=ignored):
            with self.assertRaisesRegex(RuntimeError, "rejected policy"):
                maintenance.rotate_workers(False, False)

    def test_dry_run_leaves_logs_metrics_and_cache_state_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            resin = root / "resin"
            logs = root / "logs"
            resin.mkdir()
            logs.mkdir()
            old = self.make_db(resin, "request_logs-100.db", 100, 100)
            self.make_db(resin, "request_logs-200.db", 100, 100)
            metrics = resin / "metrics.db"
            metrics.write_bytes(b"protected metrics")
            btmp = logs / "btmp.1"
            btmp.write_bytes(b"history")
            os.utime(btmp, (100, 100))
            state = root / "state"
            with patch.object(maintenance, "RESIN_LOGS", resin), \
                    patch.object(maintenance, "SYSTEM_LOGS", logs), \
                    patch.object(maintenance, "STATE", state), \
                    patch.object(maintenance, "rotate_workers", return_value=[]), \
                    patch.object(maintenance, "open_resin_files", return_value=set()), \
                    patch.object(maintenance, "command") as run:
                result = maintenance.maintain()
                self.assertEqual(result["resin_deleted"], [])
                run.assert_not_called()
            self.assertTrue(old.exists())
            self.assertTrue(btmp.exists())
            self.assertEqual(metrics.read_bytes(), b"protected metrics")
            self.assertFalse(state.exists())

    def test_apply_removes_only_expired_logs_and_cleans_cache_once_per_week(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            resin, logs, state, cache = [root / name for name in ("resin", "logs", "state", "cache")]
            for directory in (resin, logs, state, cache):
                directory.mkdir()
            old = self.make_db(resin, "request_logs-100.db", 100, 100)
            active = self.make_db(resin, "request_logs-200.db", 100, 100)
            metrics = resin / "metrics.db"
            metrics.write_bytes(b"protected metrics")
            backup = root / "postgres.sql.gz"
            backup.write_bytes(b"protected backup")
            current = logs / "btmp"
            current.write_bytes(b"current audit")
            expired = logs / "btmp.1"
            expired.write_bytes(b"expired audit")
            os.utime(expired, (100, 100))
            with patch.object(maintenance, "RESIN_LOGS", resin), \
                    patch.object(maintenance, "SYSTEM_LOGS", logs), \
                    patch.object(maintenance, "STATE", state), \
                    patch.object(maintenance, "UV_CACHE", cache), \
                    patch.object(maintenance, "rotate_workers", return_value=[]), \
                    patch.object(maintenance, "open_resin_files", return_value=set()), \
                    patch.object(maintenance, "command") as run:
                first = maintenance.maintain(True)
                self.assertEqual(first["resin_deleted"], [old.name])
                run.assert_called_once()
                self.assertNotIn("--force", run.call_args.args[0])
                second = maintenance.maintain(True)
                self.assertFalse(second["uv_cache_cleanup_due"])
                run.assert_called_once()
            self.assertFalse(old.exists())
            self.assertFalse(expired.exists())
            self.assertTrue(active.exists())
            self.assertEqual(metrics.read_bytes(), b"protected metrics")
            self.assertEqual(backup.read_bytes(), b"protected backup")
            self.assertEqual(current.read_bytes(), b"current audit")


if __name__ == "__main__":
    unittest.main()
