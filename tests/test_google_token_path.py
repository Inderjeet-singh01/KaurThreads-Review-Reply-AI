"""Tests for resolving the legacy GOOGLE_TOKEN_FILE path.

Google credentials are stored in PostgreSQL (see tests/test_token_store.py);
the token file is only the default source of `python -m app.auth.import_token`.

    python -m unittest tests.test_google_token_path
"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.config import CREDENTIALS_DIR, PROJECT_ROOT, resolve_google_token_file

PRODUCTION_PATH = "/etc/secrets/google_token.json"


class ResolveTokenPathTests(unittest.TestCase):
    def test_default_when_unset(self):
        self.assertEqual(resolve_google_token_file(""), CREDENTIALS_DIR / "google_token.json")
        self.assertEqual(resolve_google_token_file("   "), CREDENTIALS_DIR / "google_token.json")

    def test_env_override_production_path(self):
        self.assertEqual(resolve_google_token_file(PRODUCTION_PATH), Path(PRODUCTION_PATH))

    def test_whitespace_and_quotes_are_ignored(self):
        for raw in (f" {PRODUCTION_PATH} ", f'"{PRODUCTION_PATH}"', f"'{PRODUCTION_PATH}'\n"):
            self.assertEqual(resolve_google_token_file(raw), Path(PRODUCTION_PATH))

    def test_relative_path_is_anchored_at_project_root(self):
        self.assertEqual(
            resolve_google_token_file("data/token.json"), PROJECT_ROOT / "data/token.json"
        )


if __name__ == "__main__":
    unittest.main()
