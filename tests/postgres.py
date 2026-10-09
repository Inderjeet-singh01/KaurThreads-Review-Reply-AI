"""A disposable PostgreSQL database for the credential-store tests.

Uses TEST_DATABASE_URL when set (it must be a throwaway database: the
credentials table is dropped before every test), otherwise starts a local
server with ``pgserver`` (``pip install -r requirements-dev.txt``). Tests are
skipped when neither is available. Production credentials are never used.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import psycopg
from cryptography.fernet import Fernet

from app.auth import google_oauth, token_store
from app.config import settings

TEST_CLIENT_ID = "123-abc.apps.googleusercontent.com"
TEST_CLIENT_SECRET = "fake-client-secret-value"
TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()

_server = None
_url: str | None = None


def test_database_url() -> str | None:
    global _server, _url
    if _url is None:
        url = os.environ.get("TEST_DATABASE_URL", "").strip()
        if not url:
            try:
                import pgserver
            except ImportError:
                return None
            _server = pgserver.get_server(
                tempfile.mkdtemp(prefix="review-reply-pg-"), cleanup_mode="delete"
            )
            url = _server.get_uri()
        _url = url
    return _url


def drop_table(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {token_store.TABLE}")


class DatabaseTestCase(unittest.TestCase):
    """Points the token store at an empty test database."""

    def setUp(self):
        url = test_database_url()
        if url is None:
            self.skipTest(
                "no test PostgreSQL: pip install -r requirements-dev.txt or set TEST_DATABASE_URL"
            )
        self.database_url = url
        for name, value in {
            "database_url": url,
            "google_token_encryption_key": TEST_ENCRYPTION_KEY,
            "google_client_id": TEST_CLIENT_ID,
            "google_client_secret": TEST_CLIENT_SECRET,
        }.items():
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        token_store.close()
        self.addCleanup(token_store.close)
        drop_table(url)
        google_oauth.clear_credentials_cache()
        self.addCleanup(google_oauth.clear_credentials_cache)

    def simulate_restart(self) -> None:
        """Forget everything held in memory, as a new process would."""
        token_store.close()
        google_oauth.clear_credentials_cache()

    def raw_row(self) -> tuple | None:
        with psycopg.connect(self.database_url) as conn:
            if conn.execute("SELECT to_regclass(%s)", (token_store.TABLE,)).fetchone()[0] is None:
                return None  # never written: the table was not even created
            return conn.execute(
                f"SELECT encrypted_credentials, version FROM {token_store.TABLE} WHERE id = %s",
                (token_store.CREDENTIALS_ID,),
            ).fetchone()
