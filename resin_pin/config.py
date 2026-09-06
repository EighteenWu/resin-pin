from __future__ import annotations

import os
from dataclasses import dataclass


REGIONS = ("tw", "jp", "hk", "sg", "kr")
MANAGED_MARKER = "!^__resin_pin_managed__$"
NAME_PATTERN = r"^([a-z]{2})-(\d+)$"
REGION_ALIASES = {"sgp": "sg", "korea": "kr", "kor": "kr"}
MIN_SYNC_INTERVAL_SECONDS = 60
MAX_SYNC_INTERVAL_SECONDS = 7 * 86400


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    resin_url: str
    admin_token: str
    proxy_token: str
    public_host: str
    public_port: int
    listen_host: str
    listen_port: int
    state_path: str
    sync_interval_seconds: int
    sync_on_start: bool
    ui_token: str
    regions: tuple[str, ...]
    pull_token: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        listen = _env("PIN_LISTEN", "0.0.0.0:2270")
        host, _, port = listen.rpartition(":")
        admin_token = _env("RESIN_ADMIN_TOKEN")
        ui_token = _env("PIN_UI_TOKEN") or admin_token
        regions_raw = _env("PIN_REGIONS", ",".join(REGIONS))
        regions = tuple(
            REGION_ALIASES.get(item.strip().lower(), item.strip().lower())
            for item in regions_raw.split(",")
            if item.strip()
        )
        return cls(
            resin_url=_env("RESIN_URL", "http://127.0.0.1:2260").rstrip("/"),
            admin_token=admin_token,
            proxy_token=_env("RESIN_PROXY_TOKEN"),
            public_host=_env("RESIN_PUBLIC_HOST", "127.0.0.1"),
            public_port=_env_int("RESIN_PUBLIC_PORT", 2260),
            listen_host=host or "0.0.0.0",
            listen_port=int(port or "2270"),
            state_path=_env("PIN_STATE_PATH", "./data/state.json"),
            sync_interval_seconds=_env_int("PIN_SYNC_INTERVAL_SECONDS", 86400),
            sync_on_start=_env_bool("PIN_SYNC_ON_START", True),
            ui_token=ui_token,
            pull_token=_env("PIN_PULL_TOKEN") or ui_token,
            regions=regions or REGIONS,
        )

    def proxy_url(self, platform_name: str) -> str:
        user = platform_name
        password = self.proxy_token
        auth = f"{user}:{password}@" if password != "" else f"{user}@"
        return f"http://{auth}{self.public_host}:{self.public_port}"


def normalize_sync_interval(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError("sync_interval_seconds must be an integer")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("sync_interval_seconds must be an integer")
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("sync_interval_seconds must be an integer") from exc
    if seconds < 0:
        raise ValueError("sync_interval_seconds must be >= 0")
    if seconds == 0:
        return 0
    if seconds < MIN_SYNC_INTERVAL_SECONDS:
        raise ValueError(f"sync_interval_seconds must be 0 or >= {MIN_SYNC_INTERVAL_SECONDS}")
    if seconds > MAX_SYNC_INTERVAL_SECONDS:
        raise ValueError(f"sync_interval_seconds must be <= {MAX_SYNC_INTERVAL_SECONDS}")
    return seconds
