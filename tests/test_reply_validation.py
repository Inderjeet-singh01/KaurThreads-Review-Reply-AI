"""Tests for the manual "Check Reply" validation (POST /reviews/{id}/validate).

Most tests are UNIT tests: Google and the Gemini SDK client are mocked, so
nothing leaves the machine. They verify the endpoint flow, the deterministic
checks, and that Gemini is called at most once.

``LiveGeminiJudgementTests`` sends the example reviews to the real Gemini
model to observe whether the validator is too strict or too lenient. They
are skipped unless RUN_LIVE_GEMINI_TESTS=1 and GEMINI_API_KEY are set:

    python -m unittest tests.test_reply_validation
    RUN_LIVE_GEMINI_TESTS=1 python -m unittest tests.test_reply_validation -v
"""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai import gemini_validator
from app.ai.gemini_validator import GeminiValidationError, validate_review_reply
from app.api import reviews as reviews_api
from app.config import settings
from app.schemas.review import MAX_REPLY_BYTES

RAW_REVIEW = {
    "reviewId": "r1",
    "reviewer": {"displayName": "Ann"},
    "starRating": "FIVE",
    "comment": "Beautiful dress and great service.",
    "createTime": "2026-09-01T10:00:00Z",
}
GOOD_REPLY = (
    "Thank you for your lovely feedback! We're so glad you loved the dress "
    "and had a great experience with our team."
)
ALL_TRUE = {
    "review_relevance": True,
    "business_relevance": True,
    "no_hallucination": True,
    "appropriate_tone": True,
    "safe_to_publish": True,
}


def _verdict(reason: str | None = None, **overrides: bool) -> SimpleNamespace:
    """Fake Gemini response carrying a JSON verdict."""
    return SimpleNamespace(text=json.dumps({**ALL_TRUE, **overrides, "reason": reason}))


class ValidateEndpointTests(unittest.TestCase):
    def setUp(self):
        gemini_patch = mock.patch.object(gemini_validator.genai, "Client")
        self.gemini_cls = gemini_patch.start()
        self.addCleanup(gemini_patch.stop)
        self.gemini_generate = self.gemini_cls.return_value.models.generate_content
        self.gemini_generate.return_value = _verdict()

        self.raw_review = dict(RAW_REVIEW)
        review_patch = mock.patch.object(
            reviews_api, "get_review", side_effect=lambda *a, **k: self.raw_review
        )
        self.get_review = review_patch.start()
        self.addCleanup(review_patch.stop)

        key_patch = mock.patch.object(settings, "gemini_api_key", "gm-test")
        key_patch.start()
        self.addCleanup(key_patch.stop)

        app = FastAPI()
        app.include_router(reviews_api.router)
        self.client = TestClient(app)

    def validate(self, reply: str, **params):
        return self.client.post("/reviews/r1/validate", json={"reply": reply}, params=params)

    # Tests 1 + 10
    def test_pass_calls_gemini_exactly_once(self):
        response = self.validate(GOOD_REPLY)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"review_id": "r1", "passed": True, "decision": "PASS", "reason": None, "checks": ALL_TRUE},
        )
        self.gemini_generate.assert_called_once()

    def test_gemini_sees_google_review_not_frontend_text(self):
        self.validate(GOOD_REPLY, location_id="loc9")
        self.get_review.assert_called_once_with("r1", location_id="loc9")
        prompt = self.gemini_generate.call_args.kwargs["contents"]
        self.assertIn("Beautiful dress and great service.", prompt)
        self.assertIn("5 out of 5", prompt)
        self.assertIn(GOOD_REPLY, prompt)
        retry = self.gemini_cls.call_args.kwargs["http_options"].retry_options
        self.assertEqual(retry.attempts, 1)

    # Tests 2 + 3: Gemini's FAIL verdict is passed through with its reason.
    def test_wrong_business_fail(self):
        self.gemini_generate.return_value = _verdict(
            "The reply refers to a restaurant meal.",
            review_relevance=False, business_relevance=False, safe_to_publish=False,
        )
        body = self.validate("Thank you for visiting our restaurant!").json()
        self.assertEqual(body["decision"], "FAIL")
        self.assertFalse(body["passed"])
        self.assertEqual(body["reason"], "The reply refers to a restaurant meal.")
        self.assertFalse(body["checks"]["business_relevance"])
        self.assertTrue(body["checks"]["no_hallucination"])

    def test_any_failed_check_forces_fail_and_unsafe(self):
        # Gemini flags a hallucination but inconsistently leaves safe_to_publish true.
        self.gemini_generate.return_value = _verdict(None, no_hallucination=False)
        body = self.validate("Glad you used our 20% festival discount!").json()
        self.assertEqual(body["decision"], "FAIL")
        self.assertFalse(body["checks"]["safe_to_publish"])
        self.assertIn("no hallucination", body["reason"])

    # Test 5: caught deterministically, no Gemini call.
    def test_ai_disclosure_fails_without_gemini(self):
        for reply in ("As an AI, I am glad you liked the dress.",
                      "This AI-generated response thanks you for visiting."):
            with self.subTest(reply=reply):
                body = self.validate(reply).json()
                self.assertEqual(body["decision"], "FAIL")
                self.assertIn("AI", body["reason"])
        self.gemini_generate.assert_not_called()

    def test_unfilled_placeholder_fails_without_gemini(self):
        body = self.validate("Thank you [Customer Name] for the kind words!").json()
        self.assertEqual(body["decision"], "FAIL")
        self.gemini_generate.assert_not_called()

    # Test 6
    def test_empty_reply_fails_without_gemini(self):
        for reply in ("", "   \n "):
            with self.subTest(reply=reply):
                response = self.validate(reply)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["decision"], "FAIL")
        self.gemini_generate.assert_not_called()

    # Test 7
    def test_over_byte_limit_fails_without_gemini(self):
        body = self.validate("é" * (MAX_REPLY_BYTES // 2 + 1)).json()
        self.assertEqual(body["decision"], "FAIL")
        self.assertIn(str(MAX_REPLY_BYTES), body["reason"])
        self.gemini_generate.assert_not_called()

    # Test 8
    def test_already_replied_returns_409_without_gemini(self):
        self.raw_review["reviewReply"] = {"comment": "Thanks!"}
        response = self.validate(GOOD_REPLY)
        self.assertEqual(response.status_code, 409)
        self.gemini_generate.assert_not_called()

    # Test 9
    def test_gemini_failure_returns_clean_502(self):
        self.gemini_generate.side_effect = RuntimeError("key=secret internal details")
        response = self.validate(GOOD_REPLY)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret", response.text)
        self.assertIn("Could not validate", response.json()["detail"])
        self.gemini_generate.assert_called_once()

    def test_unparseable_gemini_output_returns_502(self):
        self.gemini_generate.return_value = SimpleNamespace(text="PASS!")
        self.assertEqual(self.validate(GOOD_REPLY).status_code, 502)

    def test_missing_gemini_key_returns_503_but_basic_checks_still_work(self):
        with mock.patch.object(settings, "gemini_api_key", ""):
            self.assertEqual(self.validate(GOOD_REPLY).status_code, 503)
            self.assertEqual(self.validate("").json()["decision"], "FAIL")
        self.gemini_cls.assert_not_called()


class BasicChecksTests(unittest.TestCase):
    """Deterministic checks must not create false failures on normal replies."""

    def test_normal_replies_pass_basic_checks(self):
        replies = [
            GOOD_REPLY,
            "Thank you for sharing your experience! 😊",
            "We're sorry the fitting wasn't right. Please contact us so we can look into it.",
            "Thanks, Priya! Our care instructions are on the label [inside the garment].",
        ]
        for reply in replies:
            with self.subTest(reply=reply):
                self.assertIsNone(gemini_validator.basic_reply_checks("r1", reply))


@unittest.skipUnless(
    os.getenv("RUN_LIVE_GEMINI_TESTS") == "1" and settings.gemini_api_key,
    "live Gemini tests disabled (set RUN_LIVE_GEMINI_TESTS=1)",
)
class LiveGeminiJudgementTests(unittest.TestCase):
    """Real Gemini calls on the example cases. One call per case."""

    def check(self, rating: int, review: str, reply: str, expected: str):
        normalized = {"review_id": "live", "rating": rating, "reviewer": "Customer", "review": review}
        try:
            result = validate_review_reply(normalized, reply)
        except GeminiValidationError as exc:
            self.skipTest(f"Gemini unavailable: {exc}")
        self.assertEqual(result.decision, expected, msg=result.reason)

    # PASS cases: the validator must not be too strict.
    def test_pass_short_collection_review(self):
        self.check(5, "Great experience, loved the collection!",
                   "Thank you for sharing your experience! We're so glad you loved our "
                   "collection and enjoyed visiting us.", "PASS")

    def test_pass_staff_and_collection(self):
        self.check(5, "Beautiful collection and very helpful staff.",
                   "Thank you for your lovely feedback! We're so glad you enjoyed our "
                   "collection and had a great experience with our team.", "PASS")

    def test_pass_generic_negative_reply(self):
        self.check(2, "Staff was rude and I was disappointed with the fitting.",
                   "We're sorry to hear that your experience did not meet your expectations. "
                   "We appreciate you sharing your feedback and would like the opportunity "
                   "to better understand your concerns.", "PASS")

    def test_pass_rating_only(self):
        self.check(5, "", "Thank you so much for the five-star rating! We hope to see you again soon.", "PASS")

    def test_pass_mixed_review(self):
        self.check(3, "Loved the design but the stitching came loose after one wash.",
                   "Thank you for your feedback. We're glad you liked the design, and we're "
                   "sorry to hear about the stitching. Please get in touch with us so we can "
                   "look into it.", "PASS")

    # FAIL cases: clearly unsuitable replies.
    def test_fail_wrong_business(self):
        self.check(5, "Beautiful dress and great service.",
                   "Thank you for visiting our restaurant! We're glad you enjoyed your meal.", "FAIL")

    def test_fail_invented_discount(self):
        self.check(5, "Nice collection.", "Thank you! We're glad you used our 20% festival discount.", "FAIL")

    def test_fail_invented_action(self):
        self.check(1, "The dress tore on the first day.",
                   "We're sorry. Our manager has already contacted you and resolved the issue.", "FAIL")

    def test_fail_promised_refund(self):
        self.check(1, "The dress tore on the first day.",
                   "We're so sorry! We will give you a full refund and a free dress.", "FAIL")

    def test_fail_contradicts_review(self):
        self.check(1, "Rude staff and the dress didn't fit at all.",
                   "Thank you for your wonderful review! We're thrilled you loved everything.", "FAIL")


if __name__ == "__main__":
    unittest.main()
