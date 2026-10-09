"""Tests for database-backed Google OAuth credential persistence.

Runs against a real, disposable PostgreSQL (see tests/postgres.py). Google is
mocked; no production credentials or databases are used.

    python -m unittest tests.test_token_store
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from app.auth import google_oauth, import_token, token_store
from app.automation import processor
from app.automation.processor import AutomationStatus, process_new_review
from app.config import PROJECT_ROOT, settings
from tests.postgres import (
    TEST_CLIENT_ID,
    TEST_CLIENT_SECRET,
    TEST_ENCRYPTION_KEY,
    DatabaseTestCase,
)
from tests.test_automation import GOOD_REPLY, LOCATION_ID, PASS, RAW_REVIEW, REVIEW_ID

DB_PASSWORD = "sup3r-secret-db-password"
UNREACHABLE_DB = f"postgresql://app:{DB_PASSWORD}@127.0.0.1:1/neondb?sslmode=disable"


def _expiry(hours: float) -> datetime.datetime:
    """Naive UTC, like google-auth."""
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=hours)).replace(tzinfo=None)


def make_credentials(token="fake-access-token-value", refresh="fake-refresh-token-value",
                     hours=1.0) -> Credentials:
    return Credentials(
        token=token,
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=TEST_CLIENT_ID,
        client_secret=TEST_CLIENT_SECRET,
        scopes=[google_oauth.GOOGLE_SCOPE_BUSINESS_MANAGE],
        expiry=_expiry(hours),
    )


SECRET_VALUES = (
    "fake-access-token-value", "fake-refresh-token-value", TEST_CLIENT_SECRET,
    "fresh-access", "new-refresh-token", TEST_ENCRYPTION_KEY, DB_PASSWORD,
)


class SecretsMixin:
    def assert_no_secrets(self, value):
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, text)


class FakeRefresh:
    """Stands in for Credentials.refresh (no network)."""

    def __init__(self, error: Exception | None = None):
        self.calls = 0
        self.error = error
        self.lock = threading.Lock()

    def __call__(self, credentials, request):
        with self.lock:
            self.calls += 1
            n = self.calls
        if self.error:
            raise self.error
        credentials.token = f"fresh-access-{n}"
        credentials.expiry = _expiry(1)

    def patch(self):
        return mock.patch.object(Credentials, "refresh", autospec=True, side_effect=self)


def stored_info() -> dict:
    stored = token_store.load()
    assert stored is not None
    return stored.info


class ApiTestCase(DatabaseTestCase, SecretsMixin):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(google_oauth.router)
        self.client = TestClient(app)
        for name in ("_current_flow", "_current_state"):
            patcher = mock.patch.object(google_oauth, name, None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def callback(self, credentials: Credentials, state="s"):
        flow = mock.MagicMock()
        flow.credentials = credentials
        with mock.patch.object(google_oauth, "_current_flow", flow), \
                mock.patch.object(google_oauth, "_current_state", "s"):
            return self.client.get(f"/auth/google/callback?code=c&state={state}")

    def status(self, **params) -> dict:
        response = self.client.get("/auth/google/status", params=params)
        self.assertEqual(response.status_code, 200)
        return response.json()


# --- 1. OAuth callback saves to PostgreSQL ----------------------------------------------
class CallbackTests(ApiTestCase):
    def test_callback_saves_encrypted_credentials(self):
        response = self.callback(make_credentials())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authenticated"])
        self.assert_no_secrets(response.json())

        ciphertext, version = self.raw_row()
        self.assertEqual(version, 1)
        self.assert_no_secrets(ciphertext)  # encrypted at rest
        info = stored_info()
        self.assertEqual(info["refresh_token"], "fake-refresh-token-value")
        self.assertEqual(info["client_id"], TEST_CLIENT_ID)
        self.assertNotIn("client_secret", info)  # taken from the environment instead

    def test_new_authorization_replaces_stored_credentials(self):
        self.callback(make_credentials())
        self.callback(make_credentials(token="a2", refresh="new-refresh-token"))
        self.assertEqual(self.raw_row()[1], 2)
        self.assertEqual(stored_info()["refresh_token"], "new-refresh-token")
        self.assertEqual(google_oauth.load_credentials().refresh_token, "new-refresh-token")

    def test_state_mismatch_is_rejected_and_nothing_stored(self):
        response = self.callback(make_credentials(), state="forged")
        self.assertEqual(response.status_code, 400)
        self.assertIn("state mismatch", response.json()["detail"])
        self.assertIsNone(self.raw_row())

    def test_callback_never_writes_a_token_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "google_token.json"
            with mock.patch.object(settings, "google_token_file", str(legacy)):
                self.assertEqual(self.callback(make_credentials()).status_code, 200)
            self.assertFalse(legacy.exists())


# --- 2. Restart ---------------------------------------------------------------------------
class RestartTests(ApiTestCase):
    def test_credentials_survive_simulated_restart(self):
        self.callback(make_credentials())
        self.simulate_restart()
        credentials = google_oauth.load_credentials()
        self.assertEqual(credentials.token, "fake-access-token-value")
        self.assertEqual(credentials.refresh_token, "fake-refresh-token-value")
        self.assertEqual(credentials.client_secret, TEST_CLIENT_SECRET)
        self.assertTrue(self.status()["authenticated"])

    def test_fresh_interpreter_loads_stored_credentials(self):
        """A new process (like a Render restart/redeploy) with only env config."""
        self.callback(make_credentials())
        token_store.close()
        env = {
            **os.environ,
            "DATABASE_URL": self.database_url,
            "GOOGLE_TOKEN_ENCRYPTION_KEY": TEST_ENCRYPTION_KEY,
            "GOOGLE_CLIENT_ID": TEST_CLIENT_ID,
            "GOOGLE_CLIENT_SECRET": TEST_CLIENT_SECRET,
            "GOOGLE_TOKEN_FILE": "/etc/secrets/google_token.json",
            "GROQ_API_KEY": "x",
        }
        code = (
            "import json\n"
            "import app.main, app.auth.google_oauth as o\n"
            "c = o.load_credentials()\n"
            "print(json.dumps({'refresh_ok': c.refresh_token == 'fake-refresh-token-value',"
            " 'status': o.get_auth_status()}))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env,
            capture_output=True, text=True, timeout=60, check=True,
        )
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(out["refresh_ok"])
        status = out["status"]
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["token_store"], "postgresql")
        self.assertTrue(status["credentials_stored"])
        self.assertTrue(status["legacy_token_file_ignored"])
        self.assert_no_secrets(status)
        self.assert_no_secrets(result.stdout + result.stderr)


# --- 3. Refresh is persisted --------------------------------------------------------------
class RefreshTests(ApiTestCase):
    def test_expired_token_is_refreshed_once_and_saved(self):
        google_oauth.save_credentials(make_credentials(hours=-2))
        self.simulate_restart()
        refresh = FakeRefresh()
        with refresh.patch():
            tokens = [google_oauth.load_credentials().token for _ in range(3)]
        self.assertEqual(tokens, ["fresh-access-1"] * 3)
        self.assertEqual(refresh.calls, 1)
        self.assertEqual(self.raw_row()[1], 2)
        self.assertEqual(stored_info()["token"], "fresh-access-1")
        self.assertEqual(stored_info()["refresh_token"], "fake-refresh-token-value")

        # After a restart the saved access token is used: no new refresh.
        self.simulate_restart()
        with refresh.patch():
            self.assertEqual(google_oauth.load_credentials().token, "fresh-access-1")
        self.assertEqual(refresh.calls, 1)

    def test_forced_refresh_after_401_is_saved(self):
        google_oauth.save_credentials(make_credentials())
        credentials = google_oauth.load_credentials()
        with FakeRefresh().patch():
            self.assertTrue(google_oauth.force_refresh_credentials(credentials))
        self.assertEqual(stored_info()["token"], "fresh-access-1")

    def test_status_reports_refresh_without_secrets(self):
        google_oauth.save_credentials(make_credentials(hours=-2))
        with FakeRefresh().patch(), self.assertLogs("app.auth", level="INFO") as logs:
            body = self.status()
        self.assertTrue(body["authenticated"])
        self.assertTrue(body["refresh_token_present"])
        self.assertFalse(body["refreshed_token_unsaved"])
        self.assertEqual(body["stored_scopes"], [google_oauth.GOOGLE_SCOPE_BUSINESS_MANAGE])
        self.assert_no_secrets(body)
        self.assert_no_secrets("\n".join(logs.output))


# --- 4. Missing configuration -----------------------------------------------------------
class MissingConfigurationTests(ApiTestCase):
    def test_missing_database_url(self):
        with mock.patch.object(settings, "database_url", ""):
            with self.assertRaises(google_oauth.GoogleTokenStoreError) as ctx:
                google_oauth.load_credentials()
            self.assertIn("DATABASE_URL is not set", str(ctx.exception))
            body = self.status()
            self.assertFalse(body["authenticated"])
            self.assertFalse(body["database_configured"])
            self.assertIn("DATABASE_URL", body["reason"])
            response = self.callback(make_credentials())
        self.assertEqual(response.status_code, 503)
        self.assertIn("DATABASE_URL", response.json()["detail"])
        self.assertIn("Nothing was stored", response.json()["detail"])

    def test_malformed_database_url_is_not_echoed(self):
        malformed = f"postgresql://app:{DB_PASSWORD}@host/db?sslmode"
        token_store.close()
        with mock.patch.object(settings, "database_url", malformed), \
                self.assertNoLogs("psycopg.pool", level="WARNING"):
            response = self.callback(make_credentials())
        self.assertEqual(response.status_code, 503)
        self.assertIn("not a valid PostgreSQL connection string", response.json()["detail"])
        self.assert_no_secrets(response.json())

    def test_missing_or_invalid_encryption_key(self):
        for value, expected in (("", "is not set"), ("not-a-key", "not a valid Fernet key")):
            with self.subTest(value=value), \
                    mock.patch.object(settings, "google_token_encryption_key", value):
                response = self.callback(make_credentials())
                self.assertEqual(response.status_code, 503)
                self.assertIn("GOOGLE_TOKEN_ENCRYPTION_KEY", response.json()["detail"])
                self.assertIn(expected, response.json()["detail"])
        self.assertIsNone(self.raw_row())

    def test_api_routes_answer_503_not_401(self):
        from app.api.reviews import _to_http_error

        with mock.patch.object(settings, "database_url", ""):
            try:
                google_oauth.load_credentials()
            except google_oauth.GoogleOAuthError as exc:
                self.assertEqual(_to_http_error(exc).status_code, 503)

    def test_wrong_encryption_key_is_a_clear_error(self):
        google_oauth.save_credentials(make_credentials())
        self.simulate_restart()
        from cryptography.fernet import Fernet

        with mock.patch.object(settings, "google_token_encryption_key", Fernet.generate_key().decode()):
            with self.assertRaises(google_oauth.GoogleTokenStoreError) as ctx:
                google_oauth.load_credentials()
        self.assertIn("could not be decrypted", str(ctx.exception))

    def test_key_rotation(self):
        google_oauth.save_credentials(make_credentials())
        self.simulate_restart()
        from cryptography.fernet import Fernet

        rotated = f"{Fernet.generate_key().decode()},{TEST_ENCRYPTION_KEY}"
        with mock.patch.object(settings, "google_token_encryption_key", rotated):
            self.assertEqual(google_oauth.load_credentials().token, "fake-access-token-value")


# --- 5. Database failures never silently lose credentials -------------------------------
class DatabaseFailureTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(token_store, "POOL_TIMEOUT_SECONDS", 1)
        patcher.start()
        self.addCleanup(patcher.stop)

    def unreachable(self):
        token_store.close()
        return mock.patch.object(settings, "database_url", UNREACHABLE_DB)

    def test_callback_fails_loudly_when_database_is_down(self):
        with self.unreachable(), self.assertLogs("app.auth", level="ERROR") as logs:
            response = self.callback(make_credentials())
        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertIn("could not be saved", detail)
        self.assertIn("Nothing was stored", detail)
        self.assert_no_secrets(detail)
        self.assert_no_secrets("\n".join(logs.output))
        self.assertIsNone(google_oauth._cache)  # not "authenticated" in memory only

    def test_status_reports_unreachable_database(self):
        with self.unreachable(), self.assertLogs("app.auth", level="ERROR"):
            body = self.status()
        self.assertFalse(body["authenticated"])
        self.assertFalse(body["database_reachable"])
        self.assert_no_secrets(body)

    def test_cached_credentials_keep_working_during_outage(self):
        google_oauth.save_credentials(make_credentials())
        google_oauth._cache.checked_at = float("-inf")  # force a database check
        with self.unreachable(), self.assertLogs("app.auth", level="WARNING"):
            self.assertEqual(google_oauth.load_credentials().token, "fake-access-token-value")

    def test_unsaved_refresh_is_reported_and_retried(self):
        google_oauth.save_credentials(make_credentials(hours=-2))
        real_save = token_store.save_if_version
        with FakeRefresh().patch(), \
                mock.patch.object(token_store, "save_if_version",
                                  side_effect=token_store.TokenStoreError("down")), \
                self.assertLogs("app.auth.google_oauth", level="WARNING"):
            self.assertEqual(google_oauth.load_credentials().token, "fresh-access-1")
            self.assertTrue(google_oauth._cache.unsaved)
            self.assertTrue(self.status()["refreshed_token_unsaved"])
        self.assertEqual(stored_info()["token"], "fake-access-token-value")

        # Database back: the next database check saves the refreshed token.
        google_oauth._cache.checked_at = float("-inf")
        with mock.patch.object(token_store, "save_if_version", side_effect=real_save):
            google_oauth.load_credentials()
        self.assertFalse(google_oauth._cache.unsaved)
        self.assertEqual(stored_info()["token"], "fresh-access-1")

    def test_redact_removes_passwords(self):
        self.assertNotIn(DB_PASSWORD, token_store.redact(f'invalid dsn "{UNREACHABLE_DB}"'))
        self.assertNotIn(DB_PASSWORD, token_store.redact(f"host=x password={DB_PASSWORD} user=a"))


# --- 6. Missing / revoked refresh tokens ------------------------------------------------
class RefreshTokenTests(ApiTestCase):
    def test_callback_without_refresh_token_keeps_stored_credentials(self):
        self.callback(make_credentials())
        response = self.callback(make_credentials(token="other", refresh=None))
        self.assertEqual(response.status_code, 400)
        self.assertIn("did not return a refresh token", response.json()["detail"])
        self.assertEqual(self.raw_row()[1], 1)
        self.assertEqual(stored_info()["refresh_token"], "fake-refresh-token-value")

    def test_revoked_refresh_token(self):
        google_oauth.save_credentials(make_credentials(hours=-2))
        self.simulate_restart()
        revoked = FakeRefresh(RefreshError("invalid_grant: Token has been expired or revoked."))
        with revoked.patch(), self.assertLogs("app.auth.google_oauth", level="ERROR"):
            with self.assertRaises(google_oauth.GoogleOAuthError) as ctx:
                google_oauth.load_credentials()
            self.assertIn("revoked", str(ctx.exception))
            self.assertNotIsInstance(ctx.exception, google_oauth.GoogleTokenStoreError)
            body = self.status()
        self.assertFalse(body["authenticated"])
        self.assertIn("Authorize again", body["reason"])
        self.assertIsNotNone(self.raw_row())  # never deleted automatically

    def test_stored_row_without_refresh_token(self):
        info = google_oauth._credentials_to_info(make_credentials())
        info.pop("refresh_token")
        token_store.save(info)
        with self.assertRaises(google_oauth.GoogleOAuthError) as ctx:
            google_oauth.load_credentials()
        self.assertIn("no refresh token", str(ctx.exception))

    def test_credentials_from_another_oauth_client(self):
        info = google_oauth._credentials_to_info(make_credentials())
        info["client_id"] = "999-other.apps.googleusercontent.com"
        token_store.save(info)
        with self.assertRaises(google_oauth.GoogleOAuthError) as ctx:
            google_oauth.load_credentials()
        self.assertIn("different OAuth client", str(ctx.exception))


# --- 7. Concurrency -----------------------------------------------------------------------
class ConcurrencyTests(ApiTestCase):
    def test_stale_refresh_never_overwrites_a_newer_authorization(self):
        google_oauth.save_credentials(make_credentials(hours=-2))
        # This process holds version 1; another instance re-authorizes (v2).
        token_store.save(google_oauth._credentials_to_info(
            make_credentials(token="other-instance", refresh="new-refresh-token")))
        refresh = FakeRefresh()
        with refresh.patch(), self.assertLogs("app.auth.google_oauth", level="WARNING") as logs:
            # The cache is still within its recheck interval: it refreshes v1...
            self.assertEqual(google_oauth.load_credentials().token, "fresh-access-1")
        self.assertTrue(any("not overwriting" in line for line in logs.output))
        # ...but the newer authorization in the database is untouched,
        self.assertEqual(self.raw_row()[1], 2)
        self.assertEqual(stored_info()["refresh_token"], "new-refresh-token")
        # and is what this process uses from the next call on.
        credentials = google_oauth.load_credentials()
        self.assertEqual(credentials.refresh_token, "new-refresh-token")
        self.assertEqual(refresh.calls, 1)

    def test_concurrent_threads_refresh_once(self):
        google_oauth.save_credentials(make_credentials(hours=-2))
        self.simulate_restart()
        refresh = FakeRefresh()
        results, errors = [], []

        def worker():
            try:
                results.append(google_oauth.load_credentials().token)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        with refresh.patch():
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(results, ["fresh-access-1"] * 8)
        self.assertEqual(refresh.calls, 1)
        self.assertEqual(self.raw_row()[1], 2)

    def test_conditional_writes_from_two_instances(self):
        """Two instances refresh the same version: exactly one write wins."""
        info = google_oauth._credentials_to_info(make_credentials())
        version = token_store.save(info)
        barrier = threading.Barrier(2)
        outcomes = []

        def instance(name):
            barrier.wait()
            outcomes.append(token_store.save_if_version({**info, "token": name}, version))

        threads = [threading.Thread(target=instance, args=(n,)) for n in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes, key=str), [2, None])
        self.assertIn(stored_info()["token"], ("a", "b"))

    def test_schema_initialization_is_idempotent(self):
        token_store.ensure_schema()
        token_store._schema_ready = False
        token_store.ensure_schema()
        self.assertIsNone(token_store.load())


# --- 8. Status / logout / verify ----------------------------------------------------------
class StatusTests(ApiTestCase):
    def test_not_connected(self):
        body = self.status()
        self.assertFalse(body["authenticated"])
        self.assertFalse(body["credentials_stored"])
        self.assertTrue(body["database_reachable"])
        self.assertIn("/auth/google/authorize", body["reason"])

    def test_verify_makes_one_google_call(self):
        google_oauth.save_credentials(make_credentials())
        with mock.patch(
            "app.google.client.GoogleBusinessClient.list_accounts",
            return_value={"accounts": [{"name": "accounts/1"}]},
        ) as list_accounts:
            body = self.status(verify="true")
        list_accounts.assert_called_once()
        self.assertTrue(body["authenticated"])
        self.assertTrue(body["google_api_ok"])
        self.assertEqual(body["google_accounts_visible"], 1)
        self.assertEqual(body["stored_version"], 1)
        self.assert_no_secrets(body)

    def test_verify_reports_google_failure(self):
        from app.google.client import GoogleAPIError

        google_oauth.save_credentials(make_credentials())
        with mock.patch(
            "app.google.client.GoogleBusinessClient.list_accounts",
            side_effect=GoogleAPIError("Google denied access", status=403),
        ):
            body = self.status(verify="true")
        self.assertTrue(body["authenticated"])
        self.assertFalse(body["google_api_ok"])
        self.assertIn("denied", body["google_api_error"])

    def test_logout_deletes_stored_credentials(self):
        self.callback(make_credentials())
        response = self.client.post("/auth/google/logout")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.raw_row())
        self.assertFalse(self.status()["authenticated"])
        with self.assertRaises(google_oauth.GoogleOAuthError):
            google_oauth.load_credentials()


# --- One-time import of an existing token file ------------------------------------------
class ImportTokenTests(DatabaseTestCase, SecretsMixin):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "google_token.json"

    def run_import(self, *args) -> tuple[int, str]:
        with mock.patch("builtins.print") as printed:
            code = import_token.main([str(self.path), *args])
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list)
        self.assert_no_secrets(output)
        return code, output

    def test_import_keeps_existing_unless_replace(self):
        self.path.write_text(make_credentials().to_json())
        self.assertEqual(self.run_import()[0], 0)
        self.assertEqual(stored_info()["refresh_token"], "fake-refresh-token-value")

        replacement = make_credentials(refresh="new-refresh-token").to_json()
        self.path.write_text(replacement)
        code, output = self.run_import()
        self.assertEqual(code, 0)
        self.assertIn("already stored", output)
        self.assertEqual(stored_info()["refresh_token"], "fake-refresh-token-value")

        self.assertEqual(self.run_import("--replace")[0], 0)
        self.assertEqual(stored_info()["refresh_token"], "new-refresh-token")
        self.assertEqual(self.path.read_text(), replacement)  # file untouched

    def test_rejects_client_secret_file(self):
        self.path.write_text(json.dumps({"web": {"client_id": "x", "client_secret": "y"}}))
        code, output = self.run_import()
        self.assertEqual(code, 1)
        self.assertIn("client secret file", output)
        self.assertIsNone(self.raw_row())


# --- 9. Automation uses the database-backed credentials ---------------------------------
def _fake_google(url_log: list, auth_log: list):
    def request(method, url, headers=None, **kwargs):
        url_log.append(url)
        auth_log.append(headers.get("Authorization"))
        if url.endswith("/accounts"):
            payload = {"accounts": [{"name": "accounts/acct1"}]}
        elif f"/reviews/{REVIEW_ID}" in url:
            payload = RAW_REVIEW
        else:
            payload = {"locations": [{"name": f"locations/{LOCATION_ID}", "title": "Shop"}]}
        return mock.MagicMock(status_code=200, content=b"{}", json=lambda: payload, url=url)

    return request


class ProcessorCredentialTests(DatabaseTestCase, SecretsMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        DatabaseTestCase.setUp(self)
        processor.reset_state()
        self.addCleanup(processor.reset_state)
        for name, value in {
            "auto_reply_enabled": True, "auto_reply_dry_run": True,
            "auto_reply_location_ids": "", "groq_api_key": "gsk-test",
        }.items():
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in (
            ("app.ai.reply_generator.generate_review_reply_groq", GOOD_REPLY),
            ("app.automation.processor.validate_review_reply", PASS),
        ):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_missing_credentials_fail_at_fetch(self):
        with self.assertLogs("app.automation.processor", level="INFO") as logs:
            run = await process_new_review(REVIEW_ID, LOCATION_ID)
        self.assertEqual(run.status, AutomationStatus.ERROR)
        self.assertEqual(run.error_stage, "fetch")
        self.assertTrue(run.retryable)
        self.assertIn("/auth/google/authorize", "\n".join(logs.output))

    async def test_stored_credentials_are_used_for_every_google_call(self):
        google_oauth.save_credentials(make_credentials())
        self.simulate_restart()
        urls, auth = [], []
        with mock.patch("app.google.client.requests.request", side_effect=_fake_google(urls, auth)), \
                self.assertLogs("app.automation.processor", level="INFO") as logs:
            run = await process_new_review(REVIEW_ID, LOCATION_ID)
        self.assertEqual(run.status, AutomationStatus.DRY_RUN, run.summary())
        self.assertTrue(any(f"/reviews/{REVIEW_ID}" in url for url in urls))
        self.assertEqual(set(auth), {"Bearer fake-access-token-value"})
        self.assert_no_secrets("\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
