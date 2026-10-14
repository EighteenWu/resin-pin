"""Host-only log/cache maintenance. Dry-run by default; never opens business DBs."""

import argparse
from contextlib import closing
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess

CONTAINERS = ("pan_so_go_worker", "pan_so_go_scheduler")
RESIN_LOGS = Path("/root/Resin/data/log")
UV_CACHE = Path("/root/.hermes/cache/uv")
UV_COMMAND = "/root/.hermes/bin/uv"
STATE = Path("/var/lib/panso-log-maintenance")
SYSTEM_LOGS = Path("/var/log")
REQUEST_DB = re.compile(r"request_logs-\d+\.db")
SIDECAR = re.compile(r"(request_logs-\d+\.db)-(wal|shm)")
BTMP_ARCHIVE = re.compile(r"btmp(?:\.\d+(?:\.gz)?|-\d{14}(?:\.gz)?)")


def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{args[0]} exited {result.returncode}: {result.stderr[-1000:]}")
    return result.stdout


def retention_cutoff(now):
    # Keep today and the preceding 13 calendar days, in the host's configured TZ.
    return (now.replace(hour=0, minute=0, second=0, microsecond=0)
            - dt.timedelta(days=13)).timestamp()


def regular_files(directory, pattern):
    if directory.is_symlink() or directory.resolve() != directory.absolute():
        raise RuntimeError(f"refusing redirected directory: {directory}")
    return sorted(p for p in directory.iterdir()
                  if pattern.fullmatch(p.name) and not p.is_symlink() and p.is_file())


def open_resin_files():
    output = command(["docker", "top", "resin", "-eo", "pid,comm"])
    opened = set()
    for line in output.splitlines()[1:]:
        fd_dir = Path("/proc") / str(int(line.split()[0])) / "fd"
        for fd in fd_dir.iterdir():
            try:
                target = os.readlink(fd)
            except FileNotFoundError:
                continue  # A descriptor can close while the process is running.
            if target.startswith("/var/log/resin/"):
                opened.add(Path(target.removesuffix(" (deleted)")).name)
    return opened


def old_resin_logs(directory, cutoff, opened):
    databases = regular_files(directory, REQUEST_DB)
    if not databases:
        return []
    # Resin reuses the greatest timestamp filename as the active DB.
    active = max(databases, key=lambda p: int(p.name.split("-")[1].split(".")[0]))
    candidates = []
    for path in databases:
        companions = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
        if path == active or any(p.name in opened for p in companions):
            continue
        wal = companions[1]
        if wal.exists() and (wal.is_symlink() or wal.stat().st_size > 0):
            continue  # Immutable reads would omit uncheckpointed log records.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            latest = db.execute("SELECT ts_ns FROM request_logs ORDER BY ts_ns DESC LIMIT 1").fetchone()
        latest_time = latest[0] / 1e9 if latest else path.stat().st_mtime
        if max(latest_time, path.stat().st_mtime) < cutoff:
            candidates.extend(p for p in companions if p.exists() and not p.is_symlink())
    for path in regular_files(directory, SIDECAR):
        parent = directory / SIDECAR.fullmatch(path.name).group(1)
        if (not parent.exists() and path.name not in opened
                and path.stat().st_mtime < cutoff):
            candidates.append(path)
    return sorted(set(candidates))


def worker_policy(container, pid):
    if container not in CONTAINERS or pid <= 0:
        raise ValueError("invalid worker container or PID")
    return f"""/proc/{pid}/root/root/.pm2/logs/*.log {{
    daily
    maxsize 20M
    rotate 14
    maxage 14
    compress
    dateext
    dateformat -%Y%m%d%H%M%S
    missingok
    notifempty
    create 0600 root root
    sharedscripts
    postrotate
        /usr/bin/docker exec {container} pm2 reloadLogs >/dev/null
    endscript
}}
"""


def rotate_workers(apply, force):
    results = []
    for container in CONTAINERS:
        pid = int(command(["docker", "inspect", "--format", "{{.State.Pid}}", container]).strip())
        policy = worker_policy(container, pid)
        fd = os.memfd_create("panso-logrotate")
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, policy.encode())
            args = ["/usr/sbin/logrotate", "--state", str(STATE / (container + ".status"))]
            if not apply:
                args.extend(["--debug", "--state", "/dev/null"])
            elif force:
                args.append("--force")
            args.append(f"/proc/self/fd/{fd}")
            checked = subprocess.run(args, pass_fds=(fd,), timeout=300,
                                     capture_output=True, text=True)
            # logrotate can return zero after ignoring an insecure config file.
            if (checked.returncode or "error:" in checked.stderr
                    or (not apply and "Handling 0 logs" in checked.stderr)):
                raise RuntimeError(f"logrotate rejected policy: {checked.stderr[-1500:]}")
        finally:
            os.close(fd)
        results.append({"container": container, "rotation": "applied" if apply else "validated"})
    return results


def maintain(apply=False, force=False):
    now = dt.datetime.now().astimezone()
    cutoff = retention_cutoff(now)
    result = {"apply": apply, "cutoff": dt.datetime.fromtimestamp(cutoff, now.tzinfo).isoformat()}
    result["workers"] = rotate_workers(apply, force)
    candidates = old_resin_logs(RESIN_LOGS, cutoff, open_resin_files())
    result["resin_deleted"] = []
    result["resin_candidates"] = [p.name for p in candidates]
    if apply:
        for path in candidates:
            if path.name in open_resin_files() or path.is_symlink():
                raise RuntimeError(f"log became active or redirected: {path.name}")
            path.unlink()
            result["resin_deleted"].append(path.name)
    archives = regular_files(SYSTEM_LOGS, BTMP_ARCHIVE)
    result["btmp_archives"] = [p.name for p in archives if p.stat().st_mtime < cutoff]
    if apply:
        for path in archives:
            if path.stat().st_mtime < cutoff:
                path.unlink()
    marker = STATE / "uv-cache-cleaned"
    due = not marker.exists() or now.timestamp() - marker.stat().st_mtime >= 7 * 86400
    result["uv_cache_cleanup_due"] = due
    if apply and due:
        if UV_CACHE.is_symlink() or UV_CACHE.resolve() != UV_CACHE.absolute():
            raise RuntimeError("refusing redirected uv cache")
        # The tool respects in-use checks. Do not force cleanup or remove installed environments.
        command([UV_COMMAND, "cache", "clean", "--cache-dir", str(UV_CACHE),
                 "--no-config", "--offline"], timeout=300)
        marker.touch(mode=0o600)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force-rotate", action="store_true")
    args = parser.parse_args()
    if args.force_rotate and not args.apply:
        parser.error("--force-rotate requires --apply")
    if args.apply:
        import fcntl
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (STATE / "maintenance.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = maintain(True, args.force_rotate)
    else:
        result = maintain()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
