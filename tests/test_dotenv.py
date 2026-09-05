from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from resin_pin.__main__ import load_dotenv


class DotenvTests(unittest.TestCase):
    def test_loads_missing_keys_only(self) -> None:
        key = "PIN_DOTENV_PROBE"
        old = os.environ.get(key)
        os.environ.pop(key, None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ".env"
                path.write_text(f"{key}=from-file\n", encoding="utf-8")
                load_dotenv(str(path))
                self.assertEqual(os.environ.get(key), "from-file")
                os.environ[key] = "already"
                path.write_text(f"{key}=ignored\n", encoding="utf-8")
                load_dotenv(str(path))
                self.assertEqual(os.environ.get(key), "already")
        finally:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
