from __future__ import annotations

from pathlib import Path

from .config import Config
from .server import serve


def load_dotenv(path: str = ".env") -> None:
    file = Path(path)
    if not file.exists():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        import os

        os.environ.setdefault(key, value)


def main() -> None:
    load_dotenv()
    cfg = Config.from_env()
    if not cfg.admin_token:
        raise SystemExit("RESIN_ADMIN_TOKEN is required")
    serve(cfg)


if __name__ == "__main__":
    main()
