"""Tests for the startup-latency work: one-query auth status, the location
resolution cache, transient-failure signalling and request timing.

    python -m unittest tests.test_startup_performance
"""

from __future__ import annotations

import json
import logging
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.auth.exceptions import TransportError

from app.auth import google_oauth, token_store
from app.google import reviews
from app.google.client import GoogleBusinessClient
from app.config import settings
from tests.postgres import DatabaseTestCase
from tests.test_token_store import SECRET_VALUES, FakeRefresh, make_credentials


def count_queries():
    """Patch token_store._run to count database statements."""
    return mock.patch.object(token_store, "_run", wraps=token_store._run)


class AuthStatusQueryTests(DatabaseTestCase):
    def test_status_costs_one_query_and_never_trusts_the_cache_alone(self):
        google_oauth.save_credentials(make_credentials())
        google_oauth.load_credentials()  # warm cache, well within the recheck interval
        with count_queries() as run:
            body = google_oauth.get_auth_status()
        self.assertTrue(body["authenticated"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(body["stored_version"], 1)
        self.assertEqual(body["stored_scopes"], [google_oauth.GOOGLE_SCOPE_BUSINESS_MANAGE])

        # Disconnected by another instance seconds ago: reported immediately.
        token_store.delete()
        body = google_oauth.get_auth_status()
        self.assertFalse(body["authenticated"])
        self.assertFalse(body["retryable"])
        self.assertFalse(body["credentials_stored"])

    def test_status_sees_a_new_authorization_immediately(self):
        google_oauth.save_credentials(make_credentials())
        google_oauth.load_credentials()
        token_store.save(google_oauth._credentials_to_info(
            make_credentials(token="other", refresh="new-refresh-token")))
        body = google_oauth.get_auth_status()
        self.assertEqual(body["stored_version"], 2)
        self.assertEqual(google_oauth.load_credentials().refresh_token, "new-refresh-token")

    def test_database_outage_is_retryable_not_signed_out(self):
        with mock.patch.object(settings, "database_url", ""):
            body = google_oauth.get_auth_status()
        self.assertFalse(body["authenticated"])
        self.assertTrue(body["retryable"])

    def test_transient_refresh_failure_is_retryable(self):
        google_oauth.save_credentials(make_credentials(hours=-2))
        with FakeRefresh(TransportError("connection reset")).patch(), \
                self.assertLogs("app.auth.google_oauth", level="ERROR"):
            body = google_oauth.get_auth_status()
            with self.assertRaises(google_oauth.GoogleTokenRefreshUnavailableError) as ctx:
                google_oauth.load_credentials()
        self.assertFalse(body["authenticated"])
        self.assertTrue(body["retryable"])
        from app.api.reviews import _to_http_error

        self.assertEqual(_to_http_error(ctx.exception).status_code, 503)

    def test_not_connected_is_not_retryable(self):
        body = google_oauth.get_auth_status()
        self.assertFalse(body["authenticated"])
        self.assertFalse(body["retryable"])


class FakeTransport:
    """Records Google calls made through a real GoogleBusinessClient."""

    def __init__(self):
        self.paths: list[str] = []

    def __call__(self, method, url, headers=None, **kwargs):
        self.paths.append(url.split("googleapis.com", 1)[1])
        if url.endswith("/accounts"):
            payload = {"accounts": [{"name": "accounts/456"}]}
        elif url.endswith("/locations"):
            payload = {"locations": [{"name": "locations/123", "title": "Shop"}]}
        else:
            payload = {"reviews": [{"reviewId": "r1"}]}
        return mock.MagicMock(status_code=200, content=b"{}", json=lambda: payload, url=url)


class LocationCacheTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        reviews.clear_location_cache()
        self.addCleanup(reviews.clear_location_cache)
        google_oauth.save_credentials(make_credentials())
        self.transport = FakeTransport()
        patcher = mock.patch("app.google.client.requests.request", side_effect=self.transport)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_second_listing_skips_account_and_location_lookups(self):
        self.assertEqual(len(reviews.get_reviews()), 1)
        self.assertEqual(len(self.transport.paths), 3)  # accounts, locations, reviews
        self.transport.paths.clear()
        self.assertEqual(len(reviews.get_reviews()), 1)
        self.assertEqual(self.transport.paths, ["/v4/accounts/456/locations/123/reviews"])

    def test_new_authorization_resolves_again(self):
        reviews.get_reviews()
        google_oauth.save_credentials(make_credentials(refresh="new-refresh-token"))
        self.transport.paths.clear()
        reviews.get_reviews()
        self.assertEqual(len(self.transport.paths), 3)

    def test_expired_entries_resolve_again(self):
        reviews.get_reviews()
        self.transport.paths.clear()
        with mock.patch.object(reviews, "LOCATION_CACHE_SECONDS", 0):
            reviews.get_reviews()
        self.assertEqual(len(self.transport.paths), 3)

    def test_clients_without_an_authorization_id_are_never_cached(self):
        client = GoogleBusinessClient(make_credentials())
        reviews.get_location_name(client)
        reviews.get_location_name(client)
        self.assertEqual(sum(p.endswith("/accounts") for p in self.transport.paths), 2)


class RequestTimingTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        # app.main validates required settings when first imported.
        for name, value in {"groq_api_key": "gsk-test", "google_redirect_uri": "http://x/cb"}.items():
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Importing app.main would install the app's logging config process-wide.
        with mock.patch("logging.basicConfig"):
            import app.main as main
        self.main = main

    def test_status_request_reports_timing_without_secrets(self):
        main = self.main

        google_oauth.save_credentials(make_credentials())
        client = TestClient(main.app)
        with self.assertLogs("app.main", level=logging.INFO) as logs:
            response = client.get("/auth/google/status?verify=false&secret=zzz")
        self.assertEqual(response.status_code, 200)
        server_timing = response.headers["Server-Timing"]
        self.assertIn('db;dur=', server_timing)
        self.assertIn('desc="1 call(s)"', server_timing)
        self.assertIn("total;dur=", server_timing)
        line = next(l for l in logs.output if "request GET /auth/google/status" in l)
        self.assertIn("db=", line)
        self.assertNotIn("zzz", line)  # no query strings in logs
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, "\n".join(logs.output) + json.dumps(response.json()))

    def test_preflight_is_not_logged(self):
        client = TestClient(self.main.app)
        with self.assertNoLogs("app.main", level=logging.INFO):
            client.options(
                "/auth/google/status",
                headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
            )
