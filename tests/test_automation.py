"""Tests for automatic new-review processing and the Pub/Sub webhook.

These are UNIT tests: Google, Groq and Gemini are mocked, so nothing leaves
the machine. Pub/Sub JWTs are real RS256 tokens signed with a throwaway key
whose certificate replaces Google's public certificates, so signature,
audience, issuer and expiry checks run through google-auth for real.

They prove the orchestration (gates, call counts, ack semantics), NOT the
quality of real AI judgements — see tests/test_reply_validation.py's live
tests for that.

    python -m unittest tests.test_automation
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import google.auth.crypt
import google.auth.jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai import prompts, reply_validator
from app.ai.gemini_client import GeminiError
from app.ai.groq_client import GroqError
from app.ai.reply_validator import ReplyValidationError
from app.api import automation as automation_api
from app.api import reviews as reviews_api
from app.automation import processor
from app.automation.processor import AutomationStatus, process_new_review
from app.config import Settings, settings
from app.google import reviews as google_reviews
from app.google.client import GoogleAPIError
from app.google.reviews import ReviewNotFoundError
from app.schemas.review import ReplyValidationResult, ValidationChecks
from app.webhooks import google_reviews as webhook
from app.webhooks.pubsub import MalformedPushError, parse_push_body

REVIEW_ID = "r1"
LOCATION_ID = "loc9"
REVIEW_NAME = f"accounts/acct1/locations/{LOCATION_ID}/reviews/{REVIEW_ID}"
AUDIENCE = "https://backend.example.com/webhooks/google-reviews"
PUSH_SA = "pubsub-push@my-project.iam.gserviceaccount.com"

RAW_REVIEW = {
    "reviewId": REVIEW_ID,
    "reviewer": {"displayName": "Ann"},
    "starRating": "FIVE",
    "comment": "Beautiful dress and great service.",
    "createTime": "2026-09-01T10:00:00Z",
}
REPLIED_REVIEW = {**RAW_REVIEW, "reviewReply": {"comment": "Thanks, Ann!"}}
RATING_ONLY_REVIEW = {**RAW_REVIEW, "comment": "", "starRating": "FOUR"}
NEGATIVE_REVIEW = {
    **RAW_REVIEW,
    "starRating": "ONE",
    "comment": "The stitching came apart after one wash and nobody helped me.",
}

GOOD_REPLY = "Thank you, Ann! We're so glad you loved the dress and our team."
FIXED_REPLY = "Thank you for the kind words about the dress and our service!"
FAIL_REASON = "The reply refers to a restaurant meal, not a fashion boutique."


def _verdict(passed: bool, reason: str | None = None) -> ReplyValidationResult:
    checks = {name: True for name in ValidationChecks.model_fields}
    if not passed:
        checks.update(business_relevance=False, safe_to_publish=False)
    return ReplyValidationResult(
        review_id=REVIEW_ID,
        passed=passed,
        decision="PASS" if passed else "FAIL",
        reason=None if passed else (reason or FAIL_REASON),
        checks=ValidationChecks(**checks),
    )


PASS = _verdict(True)
FAIL = _verdict(False)


# --- Pub/Sub helpers ----------------------------------------------------------------
def _make_key_and_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-pubsub")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return key_pem, cert.public_bytes(serialization.Encoding.PEM).decode()


_KEY_PEM, _CERT_PEM = _make_key_and_cert()
_OTHER_KEY_PEM, _ = _make_key_and_cert()
_GOOGLE_CERTS = {"test-kid": _CERT_PEM}


def _jwt(key_pem: bytes = _KEY_PEM, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": AUDIENCE,
        "email": PUSH_SA,
        "email_verified": True,
        "sub": "1234567890",
        "iat": now,
        "exp": now + 3600,
        **overrides,
    }
    signer = google.auth.crypt.RSASigner.from_string(key_pem, key_id="test-kid")
    return google.auth.jwt.encode(signer, claims).decode()


def _push_body(data: dict | str, delivery_attempt: int | None = None) -> dict:
    raw = data if isinstance(data, str) else json.dumps(data)
    body = {
        "message": {
            "data": base64.b64encode(raw.encode()).decode(),
            "messageId": "msg-1",
            "attributes": {},
        },
        "subscription": "projects/p/subscriptions/gbp-reviews-push",
    }
    if delivery_attempt is not None:
        body["deliveryAttempt"] = delivery_attempt
    return body


NEW_REVIEW_DATA = {
    "type": "NEW_REVIEW",
    "review": REVIEW_NAME,
    "location": f"accounts/acct1/locations/{LOCATION_ID}",
}


# --- Base test case ------------------------------------------------------------------
class AutomationTestCase(unittest.IsolatedAsyncioTestCase):
    """Mocks Google and both generation providers; automation fully enabled."""

    # Subclasses set this to False to run the real generator/validator code.
    mock_ai_functions = True

    def setUp(self):
        processor.reset_state()
        self.addCleanup(processor.reset_state)
        self._patch_settings(
            auto_reply_enabled=True,
            auto_reply_dry_run=False,
            auto_reply_max_regenerations=1,
            auto_reply_location_ids="",
            groq_api_key="gsk-test",
            gemini_api_key="gm-test",
            pubsub_push_audience=AUDIENCE,
            pubsub_push_service_account=PUSH_SA,
            automation_test_endpoint_enabled=False,
        )
        self.get_review = self._patch("app.automation.processor.get_review", return_value=RAW_REVIEW)
        self.publish = self._patch("app.automation.processor.publish_reply", return_value={})
        if not self.mock_ai_functions:
            return
        self.groq = self._patch(
            "app.ai.reply_generator.generate_review_reply_groq", return_value=GOOD_REPLY
        )
        self.gemini = self._patch(
            "app.ai.reply_generator.generate_review_reply_gemini", return_value=GOOD_REPLY
        )
        self.validate = self._patch(
            "app.automation.processor.validate_review_reply", return_value=PASS
        )

    def _patch(self, target: str, **kwargs) -> mock.MagicMock:
        patcher = mock.patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _patch_settings(self, **values) -> None:
        for name, value in values.items():
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def run_review(self, **kwargs):
        return await process_new_review(REVIEW_ID, LOCATION_ID, REVIEW_NAME, **kwargs)

    def assert_not_published(self, run):
        self.publish.assert_not_called()
        self.assertFalse(run.published)


# --- Processor ------------------------------------------------------------------------
class ProcessorFlowTests(AutomationTestCase):
    # 1 / 16
    async def test_new_unanswered_review_is_published(self):
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.PUBLISHED)
        self.assertFalse(run.retryable)
        # Initial fetch + final check, both against Google.
        self.assertEqual(self.get_review.call_count, 2)
        self.get_review.assert_called_with(REVIEW_ID, location_id=LOCATION_ID)
        self.validate.assert_called_once()
        self.publish.assert_called_once_with(REVIEW_ID, GOOD_REPLY, location_id=LOCATION_ID)
        self.assertEqual(run.validation_results, ["PASS"])
        self.assertEqual(run.publish_result, "published")

    # 2
    async def test_already_replied_review_stops_before_any_ai_call(self):
        self.get_review.return_value = REPLIED_REVIEW
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.SKIPPED_ALREADY_REPLIED)
        self.get_review.assert_called_once()
        self.groq.assert_not_called()
        self.gemini.assert_not_called()
        self.validate.assert_not_called()
        self.assert_not_published(run)

    # 3
    async def test_groq_success_does_not_use_gemini(self):
        run = await self.run_review()
        self.groq.assert_called_once()
        self.gemini.assert_not_called()
        self.assertEqual(run.generation_provider, "groq")

    # 4
    async def test_groq_failure_falls_back_to_gemini(self):
        self.groq.side_effect = GroqError("RateLimitError (HTTP 429)")
        run = await self.run_review()
        self.groq.assert_called_once()
        self.gemini.assert_called_once()
        self.assertEqual(run.generation_provider, "gemini")
        self.assertEqual(run.status, AutomationStatus.PUBLISHED)

    # 5
    async def test_both_generation_providers_fail(self):
        self.groq.side_effect = GroqError("APITimeoutError")
        self.gemini.side_effect = GeminiError("ServerError (HTTP 503)")
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.FAILED_GENERATION)
        self.assertTrue(run.retryable)
        self.validate.assert_not_called()
        self.assert_not_published(run)
        # Exactly one attempt per provider: no internal retry loop.
        self.groq.assert_called_once()
        self.gemini.assert_called_once()

    # 6
    async def test_validation_pass_means_no_regeneration(self):
        run = await self.run_review()
        self.assertEqual(self.groq.call_count, 1)
        self.assertFalse(run.regeneration_attempted)
        self.publish.assert_called_once()

    # 7 / 8
    async def test_validation_fail_regenerates_once_with_reason_then_publishes(self):
        self.groq.side_effect = [GOOD_REPLY, FIXED_REPLY]
        self.validate.side_effect = [FAIL, PASS]
        run = await self.run_review()

        self.assertEqual(self.groq.call_count, 2)
        first, second = self.groq.call_args_list
        self.assertIsNone(first.kwargs.get("revision_feedback"))
        self.assertEqual(second.kwargs["revision_feedback"], FAIL_REASON)
        self.assertEqual(self.validate.call_count, 2)
        self.assertEqual(self.validate.call_args_list[1].args[1], FIXED_REPLY)
        self.assertTrue(run.regeneration_attempted)
        self.assertEqual(run.validation_results, ["FAIL", "PASS"])
        self.assertEqual(run.status, AutomationStatus.PUBLISHED)
        # The corrected reply is what gets published.
        self.publish.assert_called_once_with(REVIEW_ID, FIXED_REPLY, location_id=LOCATION_ID)

    # 9
    async def test_second_validation_fail_stops_without_publishing(self):
        self.validate.side_effect = [FAIL, _verdict(False, "Still mentions a restaurant.")]
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.FAILED_VALIDATION)
        self.assertFalse(run.retryable)  # acknowledged: a rejected reply is not a transport failure
        self.assertEqual(self.groq.call_count, 2)  # never a third generation
        self.assertEqual(self.validate.call_count, 2)
        self.assertEqual(run.validation_reasons[-1], "Still mentions a restaurant.")
        self.assert_not_published(run)
        # Only the initial fetch: no final check for a rejected reply.
        self.get_review.assert_called_once()

    async def test_regeneration_disabled_by_config(self):
        self._patch_settings(auto_reply_max_regenerations=0)
        self.validate.return_value = FAIL
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.FAILED_VALIDATION)
        self.groq.assert_called_once()
        self.assert_not_published(run)

    # 10
    async def test_validator_api_error_is_not_treated_as_fail(self):
        self.validate.side_effect = ReplyValidationError("ServerError (HTTP 503)", 503)
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.ERROR)
        self.assertEqual(run.error_stage, "validation")
        self.assertTrue(run.retryable)
        self.groq.assert_called_once()  # no blind regeneration
        self.assertFalse(run.regeneration_attempted)
        self.assertEqual(run.validation_results, [])
        self.assert_not_published(run)

    async def test_validator_api_error_on_second_validation(self):
        self.validate.side_effect = [FAIL, ReplyValidationError("timeout")]
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.ERROR)
        self.assertEqual(run.error_stage, "revalidation")
        self.assertEqual(self.groq.call_count, 2)
        self.assert_not_published(run)

    # 11
    async def test_final_google_check_finds_new_reply(self):
        self.get_review.side_effect = [RAW_REVIEW, REPLIED_REVIEW]
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.SKIPPED_ALREADY_REPLIED)
        self.assertEqual(run.publish_result, "blocked_existing_reply")
        self.assertEqual(self.get_review.call_count, 2)
        self.assert_not_published(run)

    async def test_google_fetch_failure_is_retryable_and_skips_ai(self):
        self.get_review.side_effect = GoogleAPIError("Google API request failed with HTTP 500", status=500)
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.ERROR)
        self.assertEqual(run.error_stage, "fetch")
        self.assertTrue(run.retryable)
        self.groq.assert_not_called()
        self.validate.assert_not_called()
        self.assert_not_published(run)

    async def test_deleted_review_is_acknowledged(self):
        self.get_review.side_effect = ReviewNotFoundError("Review r1 was not found on Google.")
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.SKIPPED_NOT_FOUND)
        self.assertFalse(run.retryable)
        self.groq.assert_not_called()

    async def test_transient_publish_failure_is_retryable_and_not_success(self):
        self.publish.side_effect = GoogleAPIError("Publishing the reply to Google failed", status=503)
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.ERROR)
        self.assertEqual(run.error_stage, "publish")
        self.assertEqual(run.publish_result, "failed")
        self.assertTrue(run.retryable)
        self.assertFalse(run.published)

    async def test_permanent_publish_failure_is_acknowledged(self):
        self.publish.side_effect = GoogleAPIError("bad request", status=400)
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.ERROR)
        self.assertFalse(run.retryable)
        self.assertFalse(run.published)

    async def test_non_new_review_event_is_ignored(self):
        run = await self.run_review(event_type="UPDATED_REVIEW")
        self.assertEqual(run.status, AutomationStatus.IGNORED)
        self.get_review.assert_not_called()

    async def test_location_allowlist(self):
        self._patch_settings(auto_reply_location_ids="other-location")
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.IGNORED)
        self.get_review.assert_not_called()

        self._patch_settings(auto_reply_location_ids=f"accounts/acct1/locations/{LOCATION_ID}")
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.PUBLISHED)


class SafetySwitchTests(AutomationTestCase):
    # 14
    async def test_disabled_never_publishes_or_calls_ai(self):
        self._patch_settings(auto_reply_enabled=False)
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.DISABLED)
        self.assertFalse(run.retryable)
        self.get_review.assert_not_called()
        self.groq.assert_not_called()
        self.validate.assert_not_called()
        self.assert_not_published(run)

    async def test_disabled_ignores_dry_run_flag_too(self):
        self._patch_settings(auto_reply_enabled=False, auto_reply_dry_run=True)
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.DISABLED)
        self.groq.assert_not_called()

    # 15
    async def test_dry_run_runs_everything_but_publish(self):
        self._patch_settings(auto_reply_dry_run=True)
        with self.assertLogs("app.automation.processor", level="INFO") as logs:
            run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.DRY_RUN)
        self.assertEqual(run.publish_result, "dry_run")
        self.assertEqual(self.get_review.call_count, 2)  # final check still runs
        self.validate.assert_called_once()
        self.assert_not_published(run)
        self.assertTrue(any("WOULD_PUBLISH" in line for line in logs.output))

    async def test_dry_run_with_regeneration(self):
        self._patch_settings(auto_reply_dry_run=True)
        self.validate.side_effect = [FAIL, PASS]
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.DRY_RUN)
        self.assertEqual(self.groq.call_count, 2)
        self.assert_not_published(run)

    async def test_disabling_mid_run_blocks_publish(self):
        def disable_then_return(*args, **kwargs):
            settings.auto_reply_enabled = False
            return PASS

        self.validate.side_effect = disable_then_return
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.DISABLED)
        self.assert_not_published(run)

    def test_max_regenerations_hard_limit(self):
        with self.assertRaises(ValidationError):
            Settings(auto_reply_max_regenerations=2)
        self.assertFalse(Settings().auto_reply_enabled)  # safe default
        self.assertFalse(Settings().auto_reply_dry_run)


class DuplicateHandlingTests(AutomationTestCase):
    # 12
    async def test_duplicate_while_processing_is_blocked_by_lock(self):
        release = threading.Event()
        started = threading.Event()

        def slow_get_review(*args, **kwargs):
            if not started.is_set():
                started.set()
                release.wait(5)
            return RAW_REVIEW

        self.get_review.side_effect = slow_get_review
        first = asyncio.create_task(self.run_review())
        while not started.is_set():
            await asyncio.sleep(0.01)

        second = await self.run_review()
        self.assertEqual(second.status, AutomationStatus.DUPLICATE)
        self.assertTrue(second.retryable)  # redeliver later, not lost

        release.set()
        first_run = await first
        self.assertEqual(first_run.status, AutomationStatus.PUBLISHED)
        self.groq.assert_called_once()
        self.publish.assert_called_once()

    # 13
    async def test_duplicate_after_publication_detected_on_google(self):
        first = await self.run_review()
        self.assertEqual(first.status, AutomationStatus.PUBLISHED)

        self.get_review.side_effect = None
        self.get_review.return_value = REPLIED_REVIEW  # Google now shows the reply
        second = await self.run_review()
        self.assertEqual(second.status, AutomationStatus.SKIPPED_ALREADY_REPLIED)
        self.publish.assert_called_once()  # still only the first publish
        self.groq.assert_called_once()

    async def test_redelivery_after_rejected_reply_spends_no_ai_calls(self):
        self.validate.side_effect = [FAIL, FAIL]
        first = await self.run_review()
        self.assertEqual(first.status, AutomationStatus.FAILED_VALIDATION)

        second = await self.run_review()
        self.assertEqual(second.status, AutomationStatus.DUPLICATE)
        self.assertFalse(second.retryable)
        self.assertEqual(self.groq.call_count, 2)  # unchanged
        self.get_review.assert_called_once()

    async def test_retry_budget_bounds_repeated_failures(self):
        self.groq.side_effect = GroqError("down")
        self.gemini.side_effect = GeminiError("down")
        runs = [await self.run_review() for _ in range(processor.MAX_PROCESSING_ATTEMPTS)]
        self.assertTrue(all(run.retryable for run in runs[:-1]))
        self.assertFalse(runs[-1].retryable)  # acknowledged: stop redelivery
        self.assertIn("manual review", runs[-1].error)

        extra = await self.run_review()
        self.assertEqual(extra.status, AutomationStatus.DUPLICATE)
        self.assertEqual(self.groq.call_count, processor.MAX_PROCESSING_ATTEMPTS)

    async def test_pubsub_delivery_attempt_counts_toward_budget(self):
        self.groq.side_effect = GroqError("down")
        self.gemini.side_effect = GeminiError("down")
        run = await self.run_review(delivery_attempt=processor.MAX_PROCESSING_ATTEMPTS)
        self.assertFalse(run.retryable)


class SharedLogicTests(unittest.TestCase):
    # 30 / 31: one validator, one publisher, one generator for both flows.
    def test_manual_and_automatic_flows_share_implementations(self):
        self.assertIs(processor.validate_review_reply, reply_validator.validate_review_reply)
        self.assertIs(processor.validate_review_reply, reviews_api.validate_review_reply)
        self.assertIs(processor.publish_reply, google_reviews.publish_reply)
        self.assertIs(processor.publish_reply, reviews_api.publish_reply)
        self.assertIs(processor.get_review, reviews_api.get_review)
        self.assertIs(processor.has_reply, reviews_api.has_reply)


# --- Real validator + real generator plumbing (SDKs mocked) --------------------------
def _groq_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


PASS_JSON = json.dumps({**{n: True for n in ValidationChecks.model_fields}, "reason": None})


class EndToEndSdkTests(AutomationTestCase):
    """Real generator and validator code; only the SDK clients are mocked."""

    mock_ai_functions = False

    def setUp(self):
        super().setUp()
        self.gen_groq = self._patch("app.ai.groq_client.Groq").return_value.chat.completions.create
        self.gen_gemini = self._patch(
            "app.ai.gemini_client.genai.Client"
        ).return_value.models.generate_content
        self.val_groq = self._patch("app.ai.reply_validator.Groq").return_value.chat.completions.create
        self.val_gemini = self._patch(
            "app.ai.reply_validator.genai.Client"
        ).return_value.models.generate_content
        self.val_groq.return_value = _groq_response(PASS_JSON)

    # 19
    async def test_rating_only_review(self):
        self.get_review.return_value = RATING_ONLY_REVIEW
        self.gen_groq.return_value = _groq_response("Thank you so much for the four stars!")
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.PUBLISHED)
        gen_prompt = self.gen_groq.call_args.kwargs["messages"][1]["content"]
        val_prompt = self.val_groq.call_args.kwargs["messages"][1]["content"]
        self.assertIn("rating only", gen_prompt)
        self.assertIn("rating only", val_prompt)
        self.gen_gemini.assert_not_called()
        self.val_gemini.assert_not_called()

    # 20
    async def test_negative_review_with_empathetic_reply_can_pass(self):
        self.get_review.return_value = NEGATIVE_REVIEW
        reply = (
            "We're truly sorry the stitching came apart so quickly. Please get in "
            "touch with our boutique so we can understand what happened and look into it."
        )
        self.gen_groq.return_value = _groq_response(reply)
        run = await self.run_review()
        self.assertEqual(run.status, AutomationStatus.PUBLISHED)
        self.assertIn("1 out of 5", self.gen_groq.call_args.kwargs["messages"][1]["content"])
        self.publish.assert_called_once_with(REVIEW_ID, reply, location_id=LOCATION_ID)

    async def test_token_control_one_generation_one_validation_call(self):
        self.get_review.return_value = RAW_REVIEW
        self.gen_groq.return_value = _groq_response(GOOD_REPLY)
        await self.run_review()
        self.assertEqual(self.gen_groq.call_count, 1)
        self.assertEqual(self.val_groq.call_count, 1)
        self.gen_gemini.assert_not_called()
        self.val_gemini.assert_not_called()

    async def test_deterministic_check_fails_before_any_validator_call(self):
        self.get_review.return_value = RAW_REVIEW
        self.gen_groq.side_effect = [
            _groq_response("Thank you [Customer Name] for visiting!"),
            _groq_response(GOOD_REPLY),
        ]
        run = await self.run_review()
        # First draft rejected by the placeholder check (no AI call), second
        # draft validated by exactly one AI call.
        self.assertEqual(self.val_groq.call_count, 1)
        self.assertEqual(run.validation_results, ["FAIL", "PASS"])
        regen_prompt = self.gen_groq.call_args_list[1].kwargs["messages"][1]["content"]
        self.assertIn("unfilled template placeholder", regen_prompt)
        self.assertEqual(run.status, AutomationStatus.PUBLISHED)


# --- Webhook + Pub/Sub authentication -----------------------------------------------
class WebhookTests(AutomationTestCase):
    def setUp(self):
        super().setUp()
        self._patch("google.oauth2.id_token._fetch_certs", return_value=_GOOGLE_CERTS)
        app = FastAPI()
        app.include_router(webhook.router)
        self.client = TestClient(app)

    def post(self, body, token: str | None = None):
        headers = {"Authorization": f"Bearer {token or _jwt()}"}
        return self.client.post("/webhooks/google-reviews", json=body, headers=headers)

    def test_authenticated_new_review_is_processed_and_published(self):
        response = self.post(_push_body(NEW_REVIEW_DATA))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "PUBLISHED")
        self.get_review.assert_called_with(REVIEW_ID, location_id=LOCATION_ID)
        self.publish.assert_called_once()

    def test_alternate_payload_field_names(self):
        data = {"notificationType": "NEW_REVIEW", "reviewName": REVIEW_NAME,
                "locationName": f"locations/{LOCATION_ID}"}
        response = self.post(_push_body(data))
        self.assertEqual(response.json()["status"], "PUBLISHED")

    def test_validation_failure_is_acknowledged(self):
        self.validate.return_value = FAIL
        response = self.post(_push_body(NEW_REVIEW_DATA))
        self.assertEqual(response.status_code, 200)  # 2xx: Pub/Sub must not redeliver
        self.assertEqual(response.json()["status"], "FAILED_VALIDATION")
        self.publish.assert_not_called()

    def test_transient_failure_is_negatively_acknowledged(self):
        self.get_review.side_effect = GoogleAPIError("HTTP 503", status=503)
        response = self.post(_push_body(NEW_REVIEW_DATA))
        self.assertEqual(response.status_code, 503)
        self.groq.assert_not_called()

    def test_disabled_automation_acknowledges_without_work(self):
        self._patch_settings(auto_reply_enabled=False)
        response = self.post(_push_body(NEW_REVIEW_DATA))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "DISABLED")
        self.get_review.assert_not_called()
        self.publish.assert_not_called()

    def test_other_notification_types_are_ignored(self):
        response = self.post(_push_body({**NEW_REVIEW_DATA, "type": "UPDATED_REVIEW"}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "IGNORED")
        self.get_review.assert_not_called()

    # 17
    def test_malformed_payloads_are_rejected_safely(self):
        bodies = [
            {"nope": True},
            {"message": {"data": "%%% not base64 %%%"}},
            _push_body("not json"),
            _push_body(["a", "list"]),
            _push_body({"review": REVIEW_NAME}),  # no type
            _push_body({"type": "NEW_REVIEW"}),  # no review
            _push_body({"type": "NEW_REVIEW", "review": "accounts/a/locations/l/reviews/x/../y"}),
            _push_body({**NEW_REVIEW_DATA, "location": "accounts/acct1/locations/OTHER"}),
        ]
        for body in bodies:
            with self.subTest(body=body):
                response = self.post(body)
                self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/webhooks/google-reviews", content=b"{not json",
            headers={"Authorization": f"Bearer {_jwt()}", "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        self.get_review.assert_not_called()
        self.groq.assert_not_called()
        self.publish.assert_not_called()

    # 18
    def test_unauthorized_requests_are_rejected_before_processing(self):
        cases = {
            "missing": (None, 401),
            "not bearer": ("Basic abc", 401),
            "garbage": ("Bearer not-a-jwt", 403),
            "forged signature": (f"Bearer {_jwt(_OTHER_KEY_PEM)}", 403),
            "wrong audience": (f"Bearer {_jwt(aud='https://evil.example.com')}", 403),
            "wrong service account": (f"Bearer {_jwt(email='attacker@evil.iam.gserviceaccount.com')}", 403),
            "unverified email": (f"Bearer {_jwt(email_verified=False)}", 403),
            "wrong issuer": (f"Bearer {_jwt(iss='https://evil.example.com')}", 403),
            "expired": (f"Bearer {_jwt(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)}", 403),
        }
        for label, (header, status) in cases.items():
            with self.subTest(label):
                headers = {"Authorization": header} if header else {}
                with self.assertLogs("app.webhooks.google_reviews", level="WARNING") as logs:
                    response = self.client.post(
                        "/webhooks/google-reviews", json=_push_body(NEW_REVIEW_DATA), headers=headers
                    )
                self.assertEqual(response.status_code, status)
                if header and header.startswith("Bearer "):
                    token = header.split(" ", 1)[1]
                    self.assertFalse(any(token in line for line in logs.output))
        self.get_review.assert_not_called()
        self.groq.assert_not_called()
        self.publish.assert_not_called()

    def test_unconfigured_auth_rejects_everything(self):
        self._patch_settings(pubsub_push_audience="")
        response = self.post(_push_body(NEW_REVIEW_DATA))
        self.assertEqual(response.status_code, 503)
        self.get_review.assert_not_called()

    def test_duplicate_in_progress_returns_409(self):
        async def busy(*args, **kwargs):
            return processor.AutomationRun(
                review_id=REVIEW_ID, location_id=LOCATION_ID, event_type="NEW_REVIEW",
                status=AutomationStatus.DUPLICATE, retryable=True,
            )

        with mock.patch.object(webhook, "process_new_review", side_effect=busy):
            response = self.post(_push_body(NEW_REVIEW_DATA))
        self.assertEqual(response.status_code, 409)


class PushParsingTests(unittest.TestCase):
    def test_extracts_review_and_location(self):
        event = parse_push_body(_push_body(NEW_REVIEW_DATA, delivery_attempt=2))
        self.assertEqual(event.event_type, "NEW_REVIEW")
        self.assertEqual(event.review_id, REVIEW_ID)
        self.assertEqual(event.location_id, LOCATION_ID)
        self.assertEqual(event.review_resource_name, REVIEW_NAME)
        self.assertEqual(event.delivery_attempt, 2)
        self.assertEqual(event.message_id, "msg-1")

    def test_type_from_attributes(self):
        body = _push_body({"review": REVIEW_NAME})
        body["message"]["attributes"] = {"type": "NEW_REVIEW"}
        self.assertEqual(parse_push_body(body).review_id, REVIEW_ID)

    def test_rejects_missing_message(self):
        with self.assertRaises(MalformedPushError):
            parse_push_body({"subscription": "x"})


# --- Revision feedback in the shared prompt -------------------------------------------
class RevisionPromptTests(unittest.TestCase):
    REVIEW = {"rating": 5, "reviewer": "Ann", "review": "Lovely fabric!"}

    def test_prompt_unchanged_without_feedback(self):
        baseline = prompts.build_user_prompt(self.REVIEW, tone="Professional", length="short")
        self.assertEqual(
            baseline,
            prompts.build_user_prompt(self.REVIEW, tone="Professional", length="short",
                                      revision_feedback=None),
        )
        self.assertEqual(baseline, prompts.build_user_prompt(
            self.REVIEW, tone="Professional", length="short", revision_feedback="   "))
        self.assertNotIn("suitability check", baseline)

    def test_feedback_is_included_and_bounded(self):
        prompt = prompts.build_user_prompt(self.REVIEW, revision_feedback=FAIL_REASON)
        self.assertIn("failed a suitability check", prompt)
        self.assertIn(FAIL_REASON, prompt)
        self.assertIn("following all existing reply-generation rules", prompt)
        self.assertTrue(prompt.rstrip().endswith("Return only the reply text."))
        long_prompt = prompts.build_user_prompt(self.REVIEW, revision_feedback="x" * 5000)
        self.assertLess(len(long_prompt), 1000)

    def test_providers_send_feedback_with_unchanged_system_prompt(self):
        with mock.patch("app.ai.groq_client.Groq") as groq_cls, \
                mock.patch.object(settings, "groq_api_key", "gsk-test"):
            create = groq_cls.return_value.chat.completions.create
            create.return_value = _groq_response(FIXED_REPLY)
            from app.ai.reply_generator import generate_review_reply

            self.assertEqual(
                generate_review_reply(self.REVIEW, revision_feedback=FAIL_REASON), FIXED_REPLY
            )
        system, user = create.call_args.kwargs["messages"]
        self.assertEqual(system["content"], prompts.SYSTEM_PROMPT)
        self.assertIn(FAIL_REASON, user["content"])


# --- Automation endpoints ---------------------------------------------------------------
class AutomationEndpointTests(AutomationTestCase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(automation_api.router)
        self.client = TestClient(app)

    def test_status_reports_switches_without_secrets(self):
        self._patch_settings(auto_reply_dry_run=True)
        body = self.client.get("/automation/status").json()
        self.assertEqual(
            {k: body[k] for k in ("enabled", "dry_run", "max_regenerations")},
            {"enabled": True, "dry_run": True, "max_regenerations": 1},
        )
        text = json.dumps(body)
        for secret in ("gsk-test", "gm-test", PUSH_SA, AUDIENCE):
            self.assertNotIn(secret, text)

    def test_test_endpoint_is_off_by_default(self):
        response = self.client.post(f"/automation/test/{REVIEW_ID}")
        self.assertEqual(response.status_code, 404)
        self.get_review.assert_not_called()

    def test_test_endpoint_uses_the_same_processor_and_gates(self):
        self._patch_settings(automation_test_endpoint_enabled=True, auto_reply_dry_run=True)
        body = self.client.post(f"/automation/test/{REVIEW_ID}?location_id={LOCATION_ID}").json()
        self.assertEqual(body["final_status"], "DRY_RUN")
        self.publish.assert_not_called()

        self._patch_settings(auto_reply_enabled=False)
        body = self.client.post(f"/automation/test/{REVIEW_ID}").json()
        self.assertEqual(body["final_status"], "DISABLED")
        self.publish.assert_not_called()


# --- Token persistence on read-only storage ----------------------------------------------
class TokenPersistenceTests(unittest.TestCase):
    def test_refresh_survives_read_only_token_file(self):
        from app.auth import google_oauth

        credentials = mock.MagicMock(expired=True, refresh_token="refresh")
        with mock.patch.object(google_oauth, "save_credentials", side_effect=PermissionError()), \
                self.assertLogs("app.auth.google_oauth", level="WARNING"):
            google_oauth.refresh_credentials_if_needed(credentials)
            self.assertTrue(google_oauth.force_refresh_credentials(credentials))
        self.assertEqual(credentials.refresh.call_count, 2)
