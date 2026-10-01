"""Tests for the canonical Google OAuth token path (GOOGLE_TOKEN_FILE).

Every component — /auth/google/status, the OAuth callback, load_credentials,
the Google client and the automation processor — must read the SAME token
file, and GOOGLE_TOKEN_FILE (e.g. a Render Secret File at
/etc/secrets/google_token.json) must override the default
credentials/google_token.json. Google is mocked; nothing leaves the machine.

    python -m unittest tests.test_google_token_path
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import google_oauth
from app.automation import processor
from app.automation.processor import AutomationStatus, process_new_review
from app.config import CREDENTIALS_DIR, PROJECT_ROOT, resolve_google_token_file, settings
from tests.test_automation import GOOD_REPLY, LOCATION_ID, PASS, RAW_REVIEW, REVIEW_ID

PRODUCTION_PATH = "/etc/secrets/google_token.json"

# Fake but well-formed authorized-user token (the format save_credentials writes).
FAKE_TOKEN = {
    "token": "fake-access-token-value",
    "refresh_token": "fake-refresh-token-value",
    "token_uri": "https://oauth2.googleapis.com/token",
    "client_id": "123-abc.apps.googleusercontent.com",
    "client_secret": "fake-client-secret-value",
    "scopes": [google_oauth.GOOGLE_SCOPE_BUSINESS_MANAGE],
    "expiry": "2099-01-01T00:00:00Z",
}
SECRET_VALUES = ("fake-access-token-value", "fake-refresh-token-value", "fake-client-secret-value")


class TokenFileTestCase(unittest.TestCase):
    """Points the canonical token path at a temp directory."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.token_path = self.dir / "google_token.json"
        patcher = mock.patch.object(google_oauth, "GOOGLE_TOKEN_FILE", self.token_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(settings, "google_token_file", str(self.token_path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_token(self, data=FAKE_TOKEN):
        self.token_path.write_text(json.dumps(data), encoding="utf-8")

    def assert_no_secrets(self, value):
        text = json.dumps(value, default=str)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, text)


# --- Path resolution -------------------------------------------------------------------
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


class ProductionEnvironmentTests(unittest.TestCase):
    """A fresh interpreter with GOOGLE_TOKEN_FILE set, exactly like Render."""

    def run_python(self, code: str, token_file: str) -> dict:
        env = {**os.environ, "GOOGLE_TOKEN_FILE": token_file}
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env,
            capture_output=True, text=True, timeout=60, check=True,
        )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_every_component_sees_the_env_path(self):
        code = (
            "import json\n"
            "import app.config, app.auth.google_oauth as o, app.main as m\n"
            "try:\n    o.load_credentials(); err = ''\n"
            "except o.GoogleOAuthError as exc:\n    err = str(exc)\n"
            "print(json.dumps({'config': str(app.config.GOOGLE_TOKEN_FILE),"
            " 'oauth': str(o.GOOGLE_TOKEN_FILE), 'main': str(m.GOOGLE_TOKEN_FILE),"
            " 'error': err, 'status': o.get_auth_status()}))\n"
        )
        env_required = {
            "GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "x", "GROQ_API_KEY": "x",
        }
        with mock.patch.dict(os.environ, env_required):
            out = self.run_python(code, PRODUCTION_PATH)
        self.assertEqual({out["config"], out["oauth"], out["main"]}, {PRODUCTION_PATH})
        self.assertIn(PRODUCTION_PATH, out["error"])
        self.assertNotIn("credentials/google_token.json", out["error"])
        status = out["status"]
        self.assertFalse(status["authenticated"])
        self.assertIn(PRODUCTION_PATH, status["reason"])
        self.assertEqual(status["configured_token_path"], PRODUCTION_PATH)
        self.assertEqual(status["token_path_source"], "GOOGLE_TOKEN_FILE")
        self.assertTrue(status["env_variable_set"])

    def test_valid_token_at_env_path_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "google_token.json"
            path.write_text(json.dumps(FAKE_TOKEN), encoding="utf-8")
            out = self.run_python(
                "import json, app.auth.google_oauth as o\n"
                "c = o.load_credentials()\n"
                "print(json.dumps({'loaded': bool(c.token), 'status': o.get_auth_status()}))\n",
                str(path),
            )
        self.assertTrue(out["loaded"])
        self.assertTrue(out["status"]["authenticated"])
        self.assertEqual(out["status"]["configured_token_path"], str(path))


# --- load_credentials / diagnostics ----------------------------------------------------
class LoadCredentialsTests(TokenFileTestCase):
    def test_missing_file_names_the_configured_path(self):
        with self.assertRaises(google_oauth.GoogleOAuthError) as ctx:
            google_oauth.load_credentials()
        self.assertIn(str(self.token_path), str(ctx.exception))

    def test_valid_file_loads(self):
        self.write_token()
        credentials = google_oauth.load_credentials()
        self.assertEqual(credentials.token, FAKE_TOKEN["token"])
        self.assertEqual(credentials.refresh_token, FAKE_TOKEN["refresh_token"])

    def test_oauth_client_secret_file_is_rejected(self):
        self.write_token({"web": {"client_id": "x", "client_secret": "fake-client-secret-value"}})
        with self.assertRaises(google_oauth.GoogleOAuthError):
            google_oauth.load_credentials()


class AuthStatusTests(TokenFileTestCase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(google_oauth.router)
        self.client = TestClient(app)

    def status(self, **params) -> dict:
        response = self.client.get("/auth/google/status", params=params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_missing_file(self):
        (self.dir / "google_token (1).json").write_text("{}")
        body = self.status()
        self.assertFalse(body["authenticated"])
        self.assertIn(str(self.token_path), body["reason"])
        self.assertNotIn("credentials/google_token.json", body["reason"])
        self.assertEqual(body["configured_token_path"], str(self.token_path))
        self.assertEqual(body["token_path_source"], "GOOGLE_TOKEN_FILE")
        self.assertFalse(body["file_exists"])
        self.assertFalse(body["file_readable"])
        self.assertTrue(body["parent_dir_exists"])
        self.assertEqual(body["similar_files_in_dir"], ["google_token (1).json"])

    def test_valid_file(self):
        self.write_token()
        body = self.status()
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["token_file"], str(self.token_path))
        self.assertTrue(body["file_exists"])
        self.assertTrue(body["file_readable"])
        self.assertEqual(body["credential_type"], "authorized_user")
        self.assertTrue(body["refresh_token_present"])
        self.assertNotIn("google_api_ok", body)
        self.assert_no_secrets(body)

    def test_client_secret_file_is_identified(self):
        self.write_token({"web": {"client_id": "x", "client_secret": "fake-client-secret-value"}})
        body = self.status()
        self.assertFalse(body["authenticated"])
        self.assertEqual(body["credential_type"], "oauth_client_config")
        self.assertFalse(body["refresh_token_present"])
        self.assert_no_secrets(body)

    def test_invalid_json(self):
        self.token_path.write_text("not json")
        body = self.status()
        self.assertFalse(body["authenticated"])
        self.assertEqual(body["credential_type"], "invalid_json")

    def test_verify_makes_one_google_call(self):
        self.write_token()
        with mock.patch(
            "app.google.client.GoogleBusinessClient.list_accounts",
            return_value={"accounts": [{"name": "accounts/1"}]},
        ) as list_accounts:
            body = self.status(verify="true")
        list_accounts.assert_called_once()
        self.assertTrue(body["google_api_ok"])
        self.assertEqual(body["google_accounts_visible"], 1)
        self.assert_no_secrets(body)

    def test_verify_reports_google_failure(self):
        from app.google.client import GoogleAPIError

        self.write_token()
        with mock.patch(
            "app.google.client.GoogleBusinessClient.list_accounts",
            side_effect=GoogleAPIError("Google denied access", status=403),
        ):
            body = self.status(verify="true")
        self.assertTrue(body["authenticated"])
        self.assertFalse(body["google_api_ok"])
        self.assertIn("denied", body["google_api_error"])

    def test_callback_on_read_only_path_is_a_clean_error(self):
        flow = mock.MagicMock()
        with mock.patch.object(google_oauth, "_current_flow", flow), \
                mock.patch.object(google_oauth, "_current_state", "s"), \
                mock.patch.object(google_oauth, "save_credentials", side_effect=PermissionError()), \
                self.assertLogs("app.auth.google_oauth", level="ERROR"):
            response = self.client.get("/auth/google/callback?code=c&state=s")
        self.assertEqual(response.status_code, 400)
        self.assertIn(str(self.token_path), response.json()["detail"])

    def test_callback_writes_the_configured_path(self):
        flow = mock.MagicMock()
        flow.credentials.to_json.return_value = json.dumps(FAKE_TOKEN)
        with mock.patch.object(google_oauth, "_current_flow", flow), \
                mock.patch.object(google_oauth, "_current_state", "s"):
            response = self.client.get("/auth/google/callback?code=c&state=s")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.token_path.is_file())


# --- Automation processor uses the same loader -----------------------------------------
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


class ProcessorCredentialPathTests(TokenFileTestCase, unittest.IsolatedAsyncioTestCase):
    """process_new_review (webhook + test endpoint) -> get_review -> load_credentials."""

    def setUp(self):
        TokenFileTestCase.setUp(self)
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

    async def test_missing_token_fails_at_fetch_naming_the_configured_path(self):
        with self.assertLogs("app.automation.processor", level="INFO") as logs:
            run = await process_new_review(REVIEW_ID, LOCATION_ID)
        self.assertEqual(run.status, AutomationStatus.ERROR)
        self.assertEqual(run.error_stage, "fetch")
        self.assertTrue(run.retryable)
        output = "\n".join(logs.output)
        self.assertIn(str(self.token_path), output)
        self.assertNotIn("credentials/google_token.json", output)

    async def test_valid_token_file_is_used_for_every_google_call(self):
        self.write_token()
        urls, auth = [], []
        with mock.patch("app.google.client.requests.request", side_effect=_fake_google(urls, auth)), \
                self.assertLogs("app.automation.processor", level="INFO") as logs:
            run = await process_new_review(REVIEW_ID, LOCATION_ID)
        self.assertEqual(run.status, AutomationStatus.DRY_RUN, run.summary())
        self.assertTrue(any(f"/reviews/{REVIEW_ID}" in url for url in urls))
        self.assertEqual(set(auth), {f"Bearer {FAKE_TOKEN['token']}"})
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
