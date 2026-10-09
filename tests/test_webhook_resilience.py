"""Resilient NEW_REVIEW handling: tolerant parsing, fallback resolution,
idempotency, reconciliation, and in-memory token reuse.

Google, Groq and Gemini are mocked (nothing leaves the machine). Google's
review state is simulated per review id, and publishing writes the reply
into that state — so the processor's "already replied" checks see exactly
what Google would show.

    python -m unittest tests.test_webhook_resilience
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import threading
import unittest
from unittest import mock
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials

from app.ai.gemini_client import GeminiError
from app.ai.groq_client import GroqError
from app.api import automation as automation_api
from app.auth import google_oauth, token_store
from app.automation import backfill, reconcile, resolver
from app.automation.processor import AutomationStatus
from app.google.client import GoogleAPIError
from app.google.resource_names import (
    parse_location_reference,
    parse_review_reference,
    sanitize_for_log,
)
from app.google.reviews import LocationNotFoundError, ReviewNotFoundError
from app.webhooks import google_reviews as webhook
from app.webhooks.pubsub import MalformedPushError, parse_push_body
from tests.postgres import TEST_CLIENT_ID, DatabaseTestCase
from tests.test_automation import (
    _GOOGLE_CERTS,
    FAIL,
    FIXED_REPLY,
    GOOD_REPLY,
    LOCATION_ID,
    PASS,
    RAW_REVIEW,
    AutomationTestCase,
    _jwt,
)

ACCOUNT_ID = "116438881246127897846"
PROD_LOCATION_ID = "12544756209946726867"
PROD_LOCATION = f"accounts/{ACCOUNT_ID}/locations/{PROD_LOCATION_ID}"
LOCATION = f"accounts/acct1/locations/{LOCATION_ID}"
SECRET = "s3cret-reconcile-value-1234567890"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _stamp(minutes_ago: float) -> str:
    return (_now() - datetime.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def _body(data, message_id: str = "msg-1", delivery_attempt: int | None = None) -> dict:
    raw = data if isinstance(data, str) else json.dumps(data)
    body = {
        "message": {
            "data": base64.b64encode(raw.encode()).decode(),
            "messageId": message_id,
            "publishTime": _stamp(0),
            "attributes": {},
        },
        "subscription": "projects/p/subscriptions/gbp-reviews-push",
    }
    if delivery_attempt is not None:
        body["deliveryAttempt"] = delivery_attempt
    return body


def _new_review(review, location=LOCATION, **extra) -> dict:
    data = {"type": "NEW_REVIEW", **extra}
    if review is not None:
        data["review"] = review
    if location is not None:
        data["location"] = location
    return data


# --- 1-12: parsing -------------------------------------------------------------------
class NotificationParsingTests(unittest.TestCase):
    def parse(self, data, **kwargs):
        return parse_push_body(_body(data, **kwargs))

    def assert_review(self, event, review_id="r1", location_id=LOCATION_ID):
        self.assertEqual(event.event_type, "NEW_REVIEW")
        self.assertEqual(event.review_id, review_id)
        self.assertEqual(event.location_id, location_id)
        self.assertIsNone(event.review_issue)

    # 1 / 8
    def test_valid_full_resource(self):
        event = self.parse(_new_review(f"{LOCATION}/reviews/r1"), delivery_attempt=3)
        self.assert_review(event)
        self.assertEqual(event.review_resource_name, f"{LOCATION}/reviews/r1")
        self.assertEqual(event.account_id, "acct1")
        self.assertEqual(event.delivery_attempt, 3)
        self.assertEqual(event.message_id, "msg-1")

    def test_production_shaped_payload(self):
        event = self.parse(_new_review(f"{PROD_LOCATION}/reviews/AbFvOqn-x_9Z", location=PROD_LOCATION))
        self.assert_review(event, "AbFvOqn-x_9Z", PROD_LOCATION_ID)

    # 2
    def test_notification_type_aliases(self):
        for key in ("notificationType", "notification_type", "Type"):
            with self.subTest(key=key):
                event = self.parse({key: "new_review", "review": f"{LOCATION}/reviews/r1"})
                self.assert_review(event)

    # 3 / 4 / 5
    def test_review_and_location_aliases(self):
        for review_key, location_key in (
            ("reviewName", "location"), ("review", "locationName"),
            ("review_name", "location_name"), ("reviewName", "locationName"),
        ):
            with self.subTest(review_key=review_key, location_key=location_key):
                event = self.parse({
                    "type": "NEW_REVIEW", review_key: f"{LOCATION}/reviews/r1",
                    location_key: f"locations/{LOCATION_ID}",
                })
                self.assert_review(event)

    # 6
    def test_trailing_and_leading_slashes_and_whitespace(self):
        event = self.parse(_new_review(f"  /{LOCATION}/reviews/r1/ \n", location=f"{LOCATION}/"))
        self.assert_review(event)

    # 7
    def test_url_encoded_resource(self):
        for value in (quote(f"{LOCATION}/reviews/r1", safe=""),
                      quote(quote(f"{LOCATION}/reviews/r1", safe=""), safe="")):
            with self.subTest(value=value):
                self.assert_review(self.parse(_new_review(value)))

    def test_api_url_and_review_object(self):
        url = f"https://mybusiness.googleapis.com/v4/{LOCATION}/reviews/r1?x=1"
        self.assert_review(self.parse(_new_review(url)))
        self.assert_review(self.parse(_new_review({"name": f"{LOCATION}/reviews/r1"})))

    # 9
    def test_short_review_resources(self):
        for value in (f"locations/{LOCATION_ID}/reviews/r1", "reviews/r1"):
            with self.subTest(value=value):
                self.assert_review(self.parse(_new_review(value)))

    # 10
    def test_raw_review_id_with_valid_location(self):
        event = self.parse(_new_review("r1"))
        self.assert_review(event)
        self.assertEqual(event.review_resource_name, f"{LOCATION}/reviews/r1")

    # 11
    def test_malformed_review_with_valid_location_is_not_rejected(self):
        for value in ("accounts/a/locations/l/reviews/x/../y", "", "reviews/", "a b c",
                      {"unexpected": 1}, 12345, "%E0%A4%A"):
            with self.subTest(value=value):
                event = self.parse(_new_review(value))
                self.assertIsNone(event.review_id)
                self.assertEqual(event.location_id, LOCATION_ID)
                self.assertTrue(event.review_issue)
        event = self.parse(_new_review(None))
        self.assertEqual(event.review_issue, "review field missing")

    # 12
    def test_malformed_review_without_valid_location_is_rejected(self):
        for data in (
            _new_review("garbage/../x", location=None),
            _new_review("r1", location=None),  # bare id needs a location
            _new_review("reviews/r1", location="not/a/location/at/all"),
            _new_review(None, location=None),
        ):
            with self.subTest(data=data), self.assertRaises(MalformedPushError):
                self.parse(data)

    def test_conflicting_location_or_account_is_rejected(self):
        with self.assertRaises(MalformedPushError):
            self.parse(_new_review(f"{LOCATION}/reviews/r1", location="locations/OTHER"))
        with self.assertRaises(MalformedPushError):
            self.parse(_new_review(f"{LOCATION}/reviews/r1",
                                   location=f"accounts/other/locations/{LOCATION_ID}"))

    def test_unwrapped_payload_and_urlsafe_base64(self):
        event = parse_push_body(_new_review(f"{LOCATION}/reviews/r1"))  # --push-no-wrapper
        self.assert_review(event)
        raw = json.dumps(_new_review(f"{LOCATION}/reviews/r1?>>")).encode()
        body = {"message": {"data": base64.urlsafe_b64encode(raw).decode().rstrip("="),
                            "messageId": "m"}}
        self.assertEqual(parse_push_body(body).location_id, LOCATION_ID)

    def test_other_types_never_raise_for_review_issues(self):
        event = self.parse({"type": "UPDATED_REVIEW", "review": {"weird": True}})
        self.assertEqual(event.event_type, "UPDATED_REVIEW")
        self.assertIsNone(event.review_id)

    def test_reference_helpers(self):
        self.assertIsNone(parse_review_reference("accounts/a/locations/l/reviews"))
        self.assertIsNone(parse_review_reference("accounts/a/locations/l/reviews/.."))
        self.assertIsNone(parse_review_reference("reviews//r1"))
        self.assertIsNone(parse_location_reference("accounts/a"))
        self.assertEqual(parse_location_reference("12544756209946726867").location_id,
                         "12544756209946726867")
        self.assertEqual(sanitize_for_log("a\nb"), "a\\nb")
        self.assertTrue(sanitize_for_log("x" * 500).endswith("…"))


class TimestampSelectionTests(unittest.TestCase):
    def test_newest_first_with_lookback_and_nanoseconds(self):
        now = _now()
        raws = [
            {"reviewId": "old", "createTime": _stamp(60 * 24 * 30)},
            {"reviewId": "mid", "createTime": _stamp(30)},
            {"reviewId": "new", "createTime": "2099-01-01T00:00:00.123456789Z"},
            {"reviewId": "edited", "createTime": _stamp(60 * 24 * 30), "updateTime": _stamp(5)},
            {"reviewId": "undated"},
            {"reviewId": "replied", "createTime": _stamp(1), "reviewReply": {"comment": "hi"}},
        ]
        picked = resolver.select_recent_unanswered(raws, lookback_minutes=60, now=now)
        self.assertEqual([c.review_id for c in picked], ["new", "mid", "edited"])
        everything = resolver.select_recent_unanswered(raws, lookback_minutes=0, now=now)
        self.assertEqual({c.review_id for c in everything}, {"new", "mid", "edited", "old", "undated"})


# --- Webhook + fallback + idempotency (13-21, 23, 24) -----------------------------------
class ResilienceTestCase(AutomationTestCase):
    """Simulated Google review state; webhook client with real JWT checks."""

    def setUp(self):
        super().setUp()
        for module in (resolver, reconcile, backfill):
            module.reset_state()
            self.addCleanup(module.reset_state)
        self._patch_settings(
            automation_backfill_delay_seconds=0,
            reconciliation_enabled=True,
            reconciliation_secret=SECRET,
            reconciliation_max_reviews=10,
            reconciliation_lookback_minutes=7 * 24 * 60,
            auto_reply_verify_after_publish=True,
        )
        self.google: dict[str, dict] = {}
        self.get_review.side_effect = self._get_review
        self.publish.side_effect = self._publish
        self.list_fail: Exception | None = None
        self._patch("app.automation.resolver.get_google_client", return_value=mock.MagicMock())
        self._patch("app.automation.resolver.get_location_name", return_value=LOCATION)
        self.list_reviews = self._patch(
            "app.automation.resolver.get_unanswered_reviews", side_effect=self._unanswered
        )
        self._patch("google.oauth2.id_token._fetch_certs", return_value=_GOOGLE_CERTS)
        app = FastAPI()
        app.include_router(webhook.router)
        app.include_router(automation_api.router)
        self.client = TestClient(app)

    # Simulated Google
    def add_review(self, review_id: str, minutes_ago: float = 1, replied: bool = False) -> None:
        raw = {**RAW_REVIEW, "reviewId": review_id, "createTime": _stamp(minutes_ago),
               "updateTime": _stamp(minutes_ago)}
        if replied:
            raw["reviewReply"] = {"comment": "Thank you!"}
        self.google[review_id] = raw

    def _get_review(self, review_id, location_id=None):
        if review_id not in self.google:
            raise ReviewNotFoundError(f"Review {review_id} was not found on Google.")
        return dict(self.google[review_id])

    def _publish(self, review_id, comment, location_id=None):
        self.google[review_id]["reviewReply"] = {"comment": comment}
        return {"comment": comment}

    def _unanswered(self, client, location_name):
        if self.list_fail is not None:
            raise self.list_fail
        return [dict(r) for r in self.google.values() if not r.get("reviewReply")]

    def published_ids(self) -> list[str]:
        return [c.args[0] for c in self.publish.call_args_list]

    def post(self, data, message_id="msg-1", delivery_attempt=None):
        return self.client.post(
            "/webhooks/google-reviews",
            json=_body(data, message_id, delivery_attempt),
            headers={"Authorization": f"Bearer {_jwt()}"},
        )


class WebhookResolutionTests(ResilienceTestCase):
    def test_exact_review_is_published_and_verified(self):
        self.add_review("r1")
        response = self.post(_new_review(f"{LOCATION}/reviews/r1"))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["status"], body["review_id"], body["resolution"]),
                         ("PUBLISHED", "r1", "exact"))
        self.assertEqual(self.published_ids(), ["r1"])
        self.list_reviews.assert_not_called()  # exact path never lists reviews

    def test_every_alias_shape_reaches_the_processor(self):
        shapes = [
            {"notificationType": "NEW_REVIEW", "reviewName": f"{LOCATION}/reviews/a1",
             "locationName": f"locations/{LOCATION_ID}"},
            {"type": "NEW_REVIEW", "review_name": "reviews/a2", "location_name": LOCATION},
            {"type": "NEW_REVIEW", "review": quote(f"{LOCATION}/reviews/a3/", safe=""),
             "location": LOCATION},
            {"type": "NEW_REVIEW", "review": "a4", "location": LOCATION},
        ]
        for index, data in enumerate(shapes, start=1):
            self.add_review(f"a{index}", minutes_ago=index)
            with self.subTest(data=data):
                response = self.post(data, message_id=f"m{index}")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["review_id"], f"a{index}")
                self.assertEqual(response.json()["resolution"], "exact")
        self.assertEqual(self.published_ids(), ["a1", "a2", "a3", "a4"])

    # 11 / 16 — the production failure: valid location, unusable review value
    def test_malformed_review_falls_back_to_the_new_unanswered_review(self):
        self.add_review("old-answered", minutes_ago=60, replied=True)
        self.add_review("new1", minutes_ago=2)
        with self.assertLogs("app.webhooks.google_reviews", level="INFO") as logs:
            response = self.post(_new_review({"something": "unexpected"}, location=LOCATION))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["status"], body["review_id"], body["resolution"]),
                         ("PUBLISHED", "new1", "fallback"))
        self.assertEqual(self.published_ids(), ["new1"])
        text = "\n".join(logs.output)
        self.assertIn("WEBHOOK_RECEIVED message_id=msg-1", text)
        self.assertIn("stage=FALLBACK_RESOLUTION", text)
        self.assertIn("review_raw=", text)
        self.assertIn("WEBHOOK_RESULT message_id=msg-1 review_id=new1", text)

    def test_path_traversal_review_is_never_used_as_an_id(self):
        self.add_review("new1")
        response = self.post(_new_review("accounts/a/locations/l/reviews/x/../y"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resolution"], "fallback")
        for call in self.get_review.call_args_list:
            self.assertEqual(call.args[0], "new1")

    # 12
    def test_malformed_review_and_no_location_is_rejected(self):
        self.add_review("new1")
        with self.assertLogs("app.webhooks.google_reviews", level="WARNING") as logs:
            response = self.post({"type": "NEW_REVIEW", "review": "x/../y"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "REJECTED_INVALID_MESSAGE")
        self.assertIn("WEBHOOK_REJECTED message_id=msg-1 stage=PARSING", "\n".join(logs.output))
        self.get_review.assert_not_called()
        self.publish.assert_not_called()

    # Synthetic / foreign locations and location-resolution failures
    def test_synthetic_test_payload_is_acknowledged_without_google_calls(self):
        self.add_review("real-id")
        location_name = self._patch("app.google.reviews.get_location_name")
        for message_id, data in (
            ("t1", {"type": "NEW_REVIEW", "location": "TEST_LOCATION", "review": "TEST_REVIEW"}),
            ("t2", _new_review(None, location="TEST_LOCATION")),  # fallback path
            ("t3", _new_review("r1", location="accounts/1/locations/YOUR_LOCATION_ID")),
        ):
            with self.subTest(data=data):
                response = self.post(data, message_id=message_id)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "IGNORED")
        location_name.assert_not_called()
        self.get_review.assert_not_called()
        self.list_reviews.assert_not_called()
        self.groq.assert_not_called()
        self.publish.assert_not_called()

    def test_configured_location_is_authoritative(self):
        self._patch_settings(google_location_id=PROD_LOCATION_ID)
        self.add_review("r1")
        response = self.post(_new_review(f"{LOCATION}/reviews/r1"))  # another location
        self.assertEqual(response.json()["status"], "IGNORED")
        self.get_review.assert_not_called()
        # A real Google notification for the configured location is processed.
        self._patch_settings(auto_reply_dry_run=True)
        response = self.post(_new_review(f"{PROD_LOCATION}/reviews/r1", location=PROD_LOCATION),
                             message_id="msg-2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "DRY_RUN")
        self.get_review.assert_called_with("r1", location_id=PROD_LOCATION_ID)
        self.publish.assert_not_called()

    def test_location_missing_from_account_is_not_retried(self):
        self.get_review.side_effect = LocationNotFoundError(
            "Location '111' (GOOGLE_LOCATION_ID) was not found in account accounts/1."
        )
        response = self.post(_new_review("accounts/1/locations/111/reviews/r1",
                                         location="accounts/1/locations/111"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ERROR")
        self.assertEqual(self.get_review.call_count, 1)
        self.publish.assert_not_called()

    def test_transient_location_lookup_failure_is_retried(self):
        try:
            raise LocationNotFoundError("Could not list locations") from GoogleAPIError(
                "unavailable", status=503)
        except LocationNotFoundError as exc:
            self.get_review.side_effect = exc
        response = self.post(_new_review(f"{LOCATION}/reviews/r1"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "ERROR")
        self.publish.assert_not_called()

    # 17
    def test_multiple_unanswered_reviews_only_newest_is_processed(self):
        self.add_review("ancient", minutes_ago=60 * 24 * 30)  # outside the 7-day window
        self.add_review("older", minutes_ago=90)
        self.add_review("newest", minutes_ago=3)
        response = self.post(_new_review("%%%"), message_id="m1")
        self.assertEqual(response.json()["review_id"], "newest")
        self.assertEqual(self.published_ids(), ["newest"])
        # A second malformed notification (another new review) takes the next one.
        response = self.post(_new_review("%%%"), message_id="m2")
        self.assertEqual(response.json()["review_id"], "older")
        # A third finds nothing inside the window: "ancient" is never touched.
        response = self.post(_new_review("%%%"), message_id="m3")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "SKIPPED_NO_UNANSWERED_REVIEW")
        self.assertEqual(self.published_ids(), ["newest", "older"])

    # 15
    def test_no_unanswered_review_is_acknowledged(self):
        self.add_review("done", replied=True)
        response = self.post(_new_review(None))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "SKIPPED_NO_UNANSWERED_REVIEW")
        self.groq.assert_not_called()
        self.publish.assert_not_called()

    def test_unknown_well_formed_review_id_never_replies_to_another_review(self):
        # e.g. a synthetic "TEST_PUSH_123" push: acknowledged, nothing published.
        self.add_review("real-id", minutes_ago=1)
        response = self.post(_new_review(f"{LOCATION}/reviews/TEST_PUSH_123"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "SKIPPED_NOT_FOUND")
        self.list_reviews.assert_not_called()
        self.groq.assert_not_called()
        self.publish.assert_not_called()

    # 14
    def test_already_replied_review_is_skipped_without_ai(self):
        self.add_review("r1", replied=True)
        response = self.post(_new_review(f"{LOCATION}/reviews/r1"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "SKIPPED_ALREADY_REPLIED")
        self.groq.assert_not_called()
        self.publish.assert_not_called()

    # 13 / 18
    def test_duplicate_and_redelivered_notifications_publish_once(self):
        self.add_review("r1")
        first = self.post(_new_review(f"{LOCATION}/reviews/r1"))
        again = self.post(_new_review(f"{LOCATION}/reviews/r1"))  # same message redelivered
        self.assertEqual(first.json()["status"], "PUBLISHED")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["status"], "SKIPPED_ALREADY_REPLIED")
        self.assertEqual(self.published_ids(), ["r1"])
        self.groq.assert_called_once()

    def test_redelivered_fallback_message_does_not_pick_another_review(self):
        self.add_review("older", minutes_ago=30)
        self.add_review("newest", minutes_ago=1)
        first = self.post(_new_review("??"), message_id="dup")
        again = self.post(_new_review("??"), message_id="dup")
        self.assertEqual(first.json()["review_id"], "newest")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["status"], "DUPLICATE")
        self.assertEqual(self.published_ids(), ["newest"])  # "older" untouched

    async def test_concurrent_duplicate_fallback_deliveries(self):
        from app.webhooks.pubsub import parse_push_body as parse

        self.add_review("newest")
        release, started = threading.Event(), threading.Event()
        original = self._get_review

        def slow(review_id, location_id=None):
            if not started.is_set():
                started.set()
                release.wait(5)
            return original(review_id, location_id)

        self.get_review.side_effect = slow
        event = parse(_body(_new_review("??"), "same"))
        first = asyncio.create_task(resolver.process_review_notification(event))
        while not started.is_set():
            await asyncio.sleep(0.01)
        second = await resolver.process_review_notification(event)
        self.assertEqual(second.status, AutomationStatus.DUPLICATE)
        self.assertTrue(second.retryable)  # 409: redelivered later
        release.set()
        self.assertEqual((await first).status, AutomationStatus.PUBLISHED)
        self.assertEqual(self.published_ids(), ["newest"])

    def test_fallback_listing_outage_is_retried_then_acknowledged(self):
        self.list_fail = GoogleAPIError("HTTP 503", status=503)
        response = self.post(_new_review("??"), message_id="m-out")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "ERROR")
        # Pub/Sub's delivery counter bounds the retries; reconciliation takes over.
        response = self.post(_new_review("??"), message_id="m-out", delivery_attempt=3)
        self.assertEqual(response.status_code, 200)
        self.publish.assert_not_called()

    def test_fallback_respects_disabled_and_allowlist(self):
        self.add_review("new1")
        self._patch_settings(auto_reply_location_ids="someone-else")
        self.assertEqual(self.post(_new_review("??"), message_id="a").json()["status"], "IGNORED")
        self._patch_settings(auto_reply_location_ids="", auto_reply_enabled=False)
        self.assertEqual(self.post(_new_review("??"), message_id="b").json()["status"], "DISABLED")
        self.list_reviews.assert_not_called()
        self.publish.assert_not_called()

    # 19 / 20 / 21 / 23 / 24 on the fallback path (same processor as exact)
    def test_groq_success_does_not_call_gemini(self):
        self.add_review("new1")
        self.post(_new_review("??"))
        self.groq.assert_called_once()
        self.gemini.assert_not_called()

    def test_groq_rate_limit_falls_back_to_gemini(self):
        self.add_review("new1")
        self.groq.side_effect = GroqError("RateLimitError (HTTP 429)")
        response = self.post(_new_review("??"))
        self.assertEqual(response.json()["status"], "PUBLISHED")
        self.gemini.assert_called_once()

    def test_both_providers_down_is_retryable_and_not_published(self):
        self.add_review("new1")
        self.groq.side_effect = GroqError("RateLimitError (HTTP 429)")
        self.gemini.side_effect = GeminiError("ResourceExhausted (HTTP 429)")
        response = self.post(_new_review("??"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "FAILED_GENERATION")
        self.publish.assert_not_called()

    def test_validation_failure_regenerates_once(self):
        self.add_review("new1")
        self.groq.side_effect = [GOOD_REPLY, FIXED_REPLY]
        self.validate.side_effect = [FAIL, PASS]
        response = self.post(_new_review("??"))
        self.assertEqual(response.json()["status"], "PUBLISHED")
        self.assertEqual(self.publish.call_args.args[1], FIXED_REPLY)
        self.assertEqual(self.groq.call_count, 2)

    def test_dry_run_never_publishes(self):
        self.add_review("new1")
        self._patch_settings(auto_reply_dry_run=True)
        response = self.post(_new_review("??"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "DRY_RUN")
        self.publish.assert_not_called()

    def test_publish_failure_is_retryable_then_never_double_published(self):
        self.add_review("r1")
        self.publish.side_effect = GoogleAPIError("Publishing failed", status=503)
        response = self.post(_new_review(f"{LOCATION}/reviews/r1"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "ERROR")
        # Google stored the reply despite the error; the redelivery must see it.
        self.google["r1"]["reviewReply"] = {"comment": GOOD_REPLY}
        self.publish.side_effect = self._publish
        response = self.post(_new_review(f"{LOCATION}/reviews/r1"))
        self.assertEqual(response.json()["status"], "SKIPPED_ALREADY_REPLIED")
        self.assertEqual(self.publish.call_count, 1)

    def test_reply_appearing_before_publish_blocks_publishing(self):
        self.add_review("r1")

        def reply_meanwhile(*args, **kwargs):
            self.google["r1"]["reviewReply"] = {"comment": "Owner replied by hand"}
            return PASS

        self.validate.side_effect = reply_meanwhile
        response = self.post(_new_review(f"{LOCATION}/reviews/r1"))
        self.assertEqual(response.json()["status"], "SKIPPED_ALREADY_REPLIED")
        self.publish.assert_not_called()

    def test_unverified_publish_still_counts_as_published(self):
        self.add_review("r1")
        self.publish.side_effect = lambda *a, **k: {}  # Google OK, reply not visible yet
        with self.assertLogs("app.automation.processor", level="WARNING"):
            response = self.post(_new_review(f"{LOCATION}/reviews/r1"))
        self.assertEqual(response.json()["status"], "PUBLISHED")


# --- 25 / 26: reconciliation ----------------------------------------------------------
class ReconciliationTests(ResilienceTestCase):
    def reconcile_post(self, headers=None, **params):
        return self.client.post("/automation/reconcile", params=params,
                                headers=headers if headers is not None
                                else {"Authorization": f"Bearer {SECRET}"})

    def test_endpoint_requires_the_secret(self):
        self.add_review("new1")
        self.assertEqual(self.reconcile_post(headers={}).status_code, 401)
        self.assertEqual(self.reconcile_post(headers={"Authorization": "Bearer wrong"}).status_code, 403)
        self.assertEqual(self.reconcile_post(headers={"X-Reconcile-Secret": "nope"}).status_code, 403)
        self.assertEqual(self.client.get("/automation/reconcile").status_code, 401)
        self._patch_settings(reconciliation_secret="short")
        self.assertEqual(self.reconcile_post().status_code, 503)
        self.publish.assert_not_called()
        self.list_reviews.assert_not_called()

    def test_reconcile_processes_recent_unanswered_newest_first(self):
        self.add_review("ancient", minutes_ago=60 * 24 * 30)
        self.add_review("answered", minutes_ago=5, replied=True)
        self.add_review("older", minutes_ago=120)
        self.add_review("newest", minutes_ago=4)
        response = self.reconcile_post(headers={"X-Reconcile-Secret": SECRET}, wait="true")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "COMPLETED")
        self.assertEqual(body["published"], 2)
        self.assertEqual([item["review_id"] for item in body["items"]], ["newest", "older"])
        self.assertEqual(self.published_ids(), ["newest", "older"])
        self.assertNotIn(SECRET, response.text)
        self.assertNotIn(GOOD_REPLY, response.text)

        # Second run: nothing left inside the window.
        body = self.reconcile_post(wait="true").json()
        self.assertEqual((body["started"], body["total"]), (False, 0))
        self.assertEqual(len(self.published_ids()), 2)

        status = self.client.get("/automation/status").json()
        self.assertEqual(status["last_reconciliation"]["job_id"], body["job_id"])
        self.assertTrue(status["reconciliation_auth_configured"])
        self.assertNotIn(SECRET, json.dumps(status))
        latest = self.client.get("/automation/reconcile", headers={"X-Reconcile-Secret": SECRET})
        self.assertEqual(latest.json()["job"]["job_id"], body["job_id"])

    def test_reconcile_caps_reviews_and_continues_after_a_failure(self):
        self._patch_settings(reconciliation_max_reviews=2)
        for index in range(4):
            self.add_review(f"r{index}", minutes_ago=index + 1)
        self.groq.side_effect = [GroqError("down"), GOOD_REPLY]
        self.gemini.side_effect = GeminiError("down")
        body = self.reconcile_post(wait="true").json()
        self.assertEqual((body["total"], body["deferred"]), (2, 2))
        outcomes = {item["review_id"]: item["outcome"] for item in body["items"]}
        self.assertEqual(outcomes, {"r0": "FAILED", "r1": "PUBLISHED"})

    def test_reconcile_respects_dry_run_and_switches(self):
        self.add_review("new1")
        self._patch_settings(auto_reply_dry_run=True)
        body = self.reconcile_post(wait="true").json()
        self.assertEqual((body["dry_run"], body["would_publish"]), (True, 1))
        self.publish.assert_not_called()
        # Remembered DRY_RUN outcome: the next run does not spend AI calls again.
        body = self.reconcile_post(wait="true").json()
        self.assertEqual((body["total"], body["skipped_recently_settled"]), (0, 1))
        self.groq.assert_called_once()

        self._patch_settings(auto_reply_enabled=False)
        self.assertEqual(self.reconcile_post().status_code, 409)
        self._patch_settings(auto_reply_enabled=True, reconciliation_enabled=False)
        self.assertEqual(self.reconcile_post().status_code, 409)

    # 26
    async def test_concurrent_reconciliation_is_rejected(self):
        self.add_review("a", minutes_ago=1)
        self.add_review("b", minutes_ago=2)
        release, started = threading.Event(), threading.Event()
        original = self._get_review

        def slow(review_id, location_id=None):
            started.set()
            release.wait(5)
            return original(review_id, location_id)

        self.get_review.side_effect = slow
        job = await reconcile.start_reconciliation(None, "test")
        while not started.is_set():
            await asyncio.sleep(0.01)
        with self.assertRaises(reconcile.ReconcileRejected) as ctx:
            await reconcile.start_reconciliation(None, "test")
        self.assertEqual((ctx.exception.status_code, ctx.exception.job_id), (409, job.job_id))

        # A webhook for the review being reconciled is a retryable DUPLICATE.
        event = parse_push_body(_body(_new_review(f"{LOCATION}/reviews/a")))
        run = await resolver.process_review_notification(event)
        self.assertEqual((run.status, run.retryable), (AutomationStatus.DUPLICATE, True))

        release.set()
        finished = await reconcile.wait_for_job(job.job_id, timeout=10)
        self.assertEqual(finished.summary()["published"], 2)
        self.assertEqual(sorted(self.published_ids()), ["a", "b"])

    async def test_reconciliation_defers_to_a_running_backfill(self):
        self.add_review("a")
        release = threading.Event()
        original = self._get_review
        self.get_review.side_effect = lambda rid, location_id=None: (
            release.wait(5), original(rid, location_id))[1]
        self._patch("app.automation.backfill._list_pending", return_value=(LOCATION_ID, ["a"]))
        job = await backfill.start_backfill(LOCATION_ID)
        with self.assertRaises(reconcile.ReconcileRejected) as ctx:
            await reconcile.start_reconciliation(None)
        self.assertEqual(ctx.exception.job_id, job.job_id)
        release.set()
        await backfill.wait_for_job(job.job_id, timeout=10)

    async def test_reconcile_unanswered_reviews_function(self):
        self.add_review("new1")
        job = await reconcile.reconcile_unanswered_reviews(timeout=10)
        self.assertEqual(job.summary()["published"], 1)
        self.assertEqual(self.published_ids(), ["new1"])

    def test_reconcile_listing_failure_is_reported(self):
        self.list_fail = GoogleAPIError("HTTP 503", status=503)
        response = self.reconcile_post()
        self.assertEqual(response.status_code, 502)
        # The slot is released: the next scheduled call can run.
        self.list_fail = None
        self.add_review("new1")
        self.assertEqual(self.reconcile_post(wait="true").json()["published"], 1)


# --- 22: expired access token, database-backed credentials ------------------------------
class ExpiredTokenTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        expired = (datetime.datetime.now(datetime.timezone.utc)
                   - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.info = {
            "token": "stale-access", "refresh_token": "refresh-1",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": TEST_CLIENT_ID,
            "scopes": [google_oauth.GOOGLE_SCOPE_BUSINESS_MANAGE], "expiry": expired,
        }
        token_store.save(self.info)
        self.refreshes = 0

    def fake_refresh(self, credentials, request):
        self.refreshes += 1
        credentials.token = f"fresh-access-{self.refreshes}"
        credentials.expiry = (datetime.datetime.now(datetime.timezone.utc)
                              + datetime.timedelta(hours=1)).replace(tzinfo=None)

    def test_expired_token_is_refreshed_once_and_reused_in_memory(self):
        with mock.patch.object(Credentials, "refresh", autospec=True, side_effect=self.fake_refresh), \
                self.assertLogs("app.auth.google_oauth", level="INFO") as logs:
            tokens = [google_oauth.load_credentials().token for _ in range(3)]
        self.assertEqual(tokens, ["fresh-access-1"] * 3)
        self.assertEqual(self.refreshes, 1)  # not once per Google call
        self.assertEqual(sum("saved to the database" in line for line in logs.output), 1)
        self.assertFalse(any("refresh-1" in line for line in logs.output))

    def test_new_stored_credentials_replace_the_cached_ones(self):
        with mock.patch.object(Credentials, "refresh", autospec=True, side_effect=self.fake_refresh):
            google_oauth.load_credentials()
            token_store.save({**self.info, "token": "reauthorized-access",
                              "expiry": "2099-01-01T00:00:00Z"})
            google_oauth._cache.checked_at = float("-inf")  # recheck interval elapsed
            self.assertEqual(google_oauth.load_credentials().token, "reauthorized-access")
        token_store.delete()
        google_oauth._cache.checked_at = float("-inf")
        with self.assertRaises(google_oauth.GoogleOAuthError):
            google_oauth.load_credentials()

    def test_refresh_failure_is_an_oauth_error_not_a_crash(self):
        with mock.patch.object(Credentials, "refresh", autospec=True,
                               side_effect=RuntimeError("invalid_grant")), \
                self.assertLogs("app.auth.google_oauth", level="ERROR"), \
                self.assertRaises(google_oauth.GoogleOAuthError):
            google_oauth.load_credentials()


if __name__ == "__main__":
    unittest.main()
