"""Tests for Google Reviews API resource-name construction.

These are UNIT tests. They verify that this application builds the correct
``accounts/{account_id}/locations/{location_id}`` resource path (and therefore
the correct ``/v4/accounts/.../locations/.../reviews`` URL) regardless of the
form Google's Business Information API returns. They do NOT contact Google and
prove nothing about live Google connectivity — see the note at the bottom of
this file and README section 12 for the real-API check.

Run with either:
    python -m unittest tests.test_review_resource
    python -m pytest tests/test_review_resource.py
"""

from __future__ import annotations

import unittest
from typing import Any

import app.google.reviews as reviews
from app.google.client import GoogleBusinessClient
from app.google.reviews import (
    LocationNotFoundError,
    _build_review_location_resource,
    _extract_location_id,
    get_location_name,
)


class FakeGBPClient:
    """Stand-in for GoogleBusinessClient that returns canned API responses."""

    def __init__(self, accounts: list[dict], locations: list[dict]):
        self._accounts = accounts
        self._locations = locations

    def list_accounts(self, page_token: str | None = None) -> dict[str, Any]:
        return {"accounts": self._accounts}

    def list_locations(
        self, account_name: str, page_token: str | None = None
    ) -> dict[str, Any]:
        return {"locations": self._locations}


class ExtractLocationIdTests(unittest.TestCase):
    def test_bare_locations_form(self):
        self.assertEqual(_extract_location_id("locations/123"), "123")

    def test_full_resource_form(self):
        self.assertEqual(
            _extract_location_id("accounts/456/locations/123"), "123"
        )

    def test_bare_id(self):
        self.assertEqual(_extract_location_id("123"), "123")

    def test_leading_and_trailing_slashes(self):
        self.assertEqual(_extract_location_id("/locations/123/"), "123")

    def test_account_only_has_no_location(self):
        self.assertEqual(_extract_location_id("accounts/456"), "")

    def test_empty(self):
        self.assertEqual(_extract_location_id(""), "")
        self.assertEqual(_extract_location_id(None), "")  # type: ignore[arg-type]


class BuildResourceTests(unittest.TestCase):
    def test_case_a_bare_location_gets_account_prefix(self):
        # CASE A: Google returns locations/123 -> accounts/456/locations/123
        self.assertEqual(
            _build_review_location_resource("accounts/456", "locations/123"),
            "accounts/456/locations/123",
        )

    def test_case_b_full_resource_unchanged(self):
        # CASE B: Google already returns the full path -> unchanged.
        self.assertEqual(
            _build_review_location_resource(
                "accounts/456", "accounts/456/locations/123"
            ),
            "accounts/456/locations/123",
        )

    def test_no_duplicated_account_segment(self):
        result = _build_review_location_resource(
            "accounts/456", "accounts/456/locations/123"
        )
        self.assertEqual(result.count("accounts/"), 1)
        self.assertEqual(result.count("locations/"), 1)

    def test_no_duplicated_location_segment(self):
        result = _build_review_location_resource("accounts/456", "locations/123")
        self.assertNotIn("locations/123/locations/123", result)

    def test_case_c_invalid_account_raises(self):
        with self.assertRaises(LocationNotFoundError):
            _build_review_location_resource("456", "locations/123")

    def test_case_c_invalid_location_raises(self):
        with self.assertRaises(LocationNotFoundError):
            _build_review_location_resource("accounts/456", "accounts/456")

    def test_case_c_empty_location_raises(self):
        with self.assertRaises(LocationNotFoundError):
            _build_review_location_resource("accounts/456", "")


class GetLocationNameTests(unittest.TestCase):
    def setUp(self):
        # Ensure auto-resolution (no pinned location) unless a test sets it.
        self._orig_location_id = reviews.settings.google_location_id
        reviews.settings.google_location_id = ""

    def tearDown(self):
        reviews.settings.google_location_id = self._orig_location_id

    def test_bare_location_from_business_information_api(self):
        # The real-world bug: Business Information API returns locations/123.
        client = FakeGBPClient(
            accounts=[{"name": "accounts/456"}],
            locations=[{"name": "locations/123", "title": "Kaur Threads"}],
        )
        self.assertEqual(
            get_location_name(client), "accounts/456/locations/123"
        )

    def test_full_resource_from_api_unchanged(self):
        client = FakeGBPClient(
            accounts=[{"name": "accounts/456"}],
            locations=[{"name": "accounts/456/locations/123"}],
        )
        self.assertEqual(
            get_location_name(client), "accounts/456/locations/123"
        )

    def test_no_accounts_raises(self):
        client = FakeGBPClient(accounts=[], locations=[])
        with self.assertRaises(LocationNotFoundError):
            get_location_name(client)

    def test_no_locations_raises(self):
        client = FakeGBPClient(accounts=[{"name": "accounts/456"}], locations=[])
        with self.assertRaises(LocationNotFoundError):
            get_location_name(client)

    def test_pinned_by_bare_id(self):
        reviews.settings.google_location_id = "123"
        client = FakeGBPClient(
            accounts=[{"name": "accounts/456"}],
            locations=[
                {"name": "locations/999"},
                {"name": "locations/123"},
            ],
        )
        self.assertEqual(
            get_location_name(client), "accounts/456/locations/123"
        )

    def test_pinned_by_full_resource_name(self):
        # Config docs promise the full resource form works too.
        reviews.settings.google_location_id = "accounts/456/locations/123"
        client = FakeGBPClient(
            accounts=[{"name": "accounts/456"}],
            locations=[
                {"name": "locations/999"},
                {"name": "locations/123"},
            ],
        )
        self.assertEqual(
            get_location_name(client), "accounts/456/locations/123"
        )

    def test_pinned_not_found_raises(self):
        reviews.settings.google_location_id = "000"
        client = FakeGBPClient(
            accounts=[{"name": "accounts/456"}],
            locations=[{"name": "locations/123"}],
        )
        with self.assertRaises(LocationNotFoundError):
            get_location_name(client)


class _FakeCreds:
    """Minimal credentials stand-in: never expired, carries a token."""

    token = "unit-test-token-not-real"
    expired = False
    refresh_token = "unit-test-refresh-not-real"


class _FakeResponse:
    def __init__(self):
        self.status_code = 200
        self.content = b"{}"
        self.url = ""

    def json(self):
        return {}


class ReviewUrlConstructionTests(unittest.TestCase):
    """Confirm the exact URL sent to Google for listing reviews."""

    def test_list_reviews_url_is_accounts_locations_reviews(self):
        captured: dict[str, Any] = {}

        def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            resp = _FakeResponse()
            resp.url = url
            return resp

        # Patch the requests.request used inside the client module.
        import app.google.client as client_mod

        original_request = client_mod.requests.request
        client_mod.requests.request = fake_request  # type: ignore[assignment]
        try:
            client = GoogleBusinessClient(_FakeCreds())  # type: ignore[arg-type]
            client.list_reviews("accounts/456/locations/123", page_size=50)
        finally:
            client_mod.requests.request = original_request  # type: ignore[assignment]

        self.assertEqual(captured["method"], "GET")
        self.assertEqual(
            captured["url"],
            "https://mybusiness.googleapis.com/v4/"
            "accounts/456/locations/123/reviews",
        )
        # The regression we are guarding against:
        self.assertNotIn(
            "/v4/locations/123/reviews", captured["url"]
        )


# ---------------------------------------------------------------------------
# NOTE ON REAL API VERIFICATION
# ---------------------------------------------------------------------------
# The tests above are UNIT tests with a fake Google client / fake transport.
# A green run proves the resource-name and URL construction are correct; it
# does NOT prove Google returns real reviews. To verify against Google:
#   1. Complete OAuth (GET /auth/google/authorize).
#   2. Ensure GBP API quota is granted and the location is verified.
#   3. Call GET /reviews and confirm a 200 with real data (or an empty list).
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
