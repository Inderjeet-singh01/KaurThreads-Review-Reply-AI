"""Tests for the manual "Check Reply" validation (POST /reviews/{id}/validate).

Most tests are UNIT tests: Google and both AI SDK clients are mocked, so
nothing leaves the machine. They verify the endpoint flow, the deterministic
checks, and the provider order: Groq first, Gemini only if Groq fails, one
call each.

``LiveJudgementTests`` sends the example reviews to the real providers to
observe whether the validator is too strict or too lenient. They are skipped
unless RUN_LIVE_AI_TESTS=1 and an AI key are set:

    python -m unittest tests.test_reply_validation
    RUN_LIVE_AI_TESTS=1 python -m unittest tests.test_reply_validation -v
"""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import groq
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

from app.ai import reply_validator
from app.ai.reply_validator import ReplyValidationError, validate_review_reply
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


def _verdict_json(reason: str | None = None, **overrides: bool) -> str:
    return json.dumps({**ALL_TRUE, **overrides, "reason": reason})


def _groq_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _groq_rate_limit() -> groq.RateLimitError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return groq.RateLimitError("rate limited", response=httpx.Response(429, request=request), body=None)


def _gemini_error(code: int) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"code": code, "message": "x", "status": "X"}})


class ValidateEndpointTests(unittest.TestCase):
    def setUp(self):
        groq_patch = mock.patch.object(reply_validator, "Groq")
        gemini_patch = mock.patch.object(reply_validator.genai, "Client")
        self.groq_cls = groq_patch.start()
        self.gemini_cls = gemini_patch.start()
        self.addCleanup(groq_patch.stop)
        self.addCleanup(gemini_patch.stop)
        self.groq_create = self.groq_cls.return_value.chat.completions.create
        self.gemini_generate = self.gemini_cls.return_value.models.generate_content
        self.groq_create.return_value = _groq_response(_verdict_json())
        self.gemini_generate.return_value = SimpleNamespace(text=_verdict_json())

        self.raw_review = dict(RAW_REVIEW)
        review_patch = mock.patch.object(
            reviews_api, "get_review", side_effect=lambda *a, **k: self.raw_review
        )
        self.get_review = review_patch.start()
        self.addCleanup(review_patch.stop)

        for name, value in (("groq_api_key", "gsk-test"), ("gemini_api_key", "gm-test")):
            key_patch = mock.patch.object(settings, name, value)
            key_patch.start()
            self.addCleanup(key_patch.stop)

        app = FastAPI()
        app.include_router(reviews_api.router)
        self.client = TestClient(app)

    def validate(self, reply: str, **params):
        return self.client.post("/reviews/r1/validate", json={"reply": reply}, params=params)

    def assert_no_ai_calls(self):
        self.groq_create.assert_not_called()
        self.gemini_generate.assert_not_called()

    # --- Provider order -----------------------------------------------------

    def test_pass_uses_groq_once_and_never_gemini(self):
        response = self.validate(GOOD_REPLY)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"review_id": "r1", "passed": True, "decision": "PASS", "reason": None, "checks": ALL_TRUE},
        )
        self.groq_create.assert_called_once()
        self.gemini_cls.assert_not_called()

    def test_groq_sees_google_review_in_json_mode_without_retries(self):
        self.validate(GOOD_REPLY, location_id="loc9")
        self.get_review.assert_called_once_with("r1", location_id="loc9")
        kwargs = self.groq_create.call_args.kwargs
        prompt = kwargs["messages"][1]["content"]
        self.assertIn("Beautiful dress and great service.", prompt)
        self.assertIn("5 out of 5", prompt)
        self.assertIn(GOOD_REPLY, prompt)
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(self.groq_cls.call_args.kwargs["max_retries"], 0)

    def test_groq_rate_limit_falls_back_to_gemini_once(self):
        self.groq_create.side_effect = _groq_rate_limit()
        self.gemini_generate.return_value = SimpleNamespace(
            text=_verdict_json("Wrong business.", business_relevance=False)
        )
        body = self.validate(GOOD_REPLY).json()
        self.assertEqual(body["decision"], "FAIL")
        self.assertEqual(body["reason"], "Wrong business.")
        self.groq_create.assert_called_once()
        self.gemini_generate.assert_called_once()
        self.assertEqual(self.gemini_cls.call_args.kwargs["http_options"].retry_options.attempts, 1)

    def test_unparseable_groq_output_falls_back_to_gemini(self):
        self.groq_create.return_value = _groq_response("PASS!")
        self.assertEqual(self.validate(GOOD_REPLY).json()["decision"], "PASS")
        self.gemini_generate.assert_called_once()

    def test_groq_key_missing_uses_gemini(self):
        with mock.patch.object(settings, "groq_api_key", ""):
            self.assertEqual(self.validate(GOOD_REPLY).json()["decision"], "PASS")
        self.groq_cls.assert_not_called()
        self.gemini_generate.assert_called_once()

    def test_gemini_key_missing_and_groq_succeeds(self):
        with mock.patch.object(settings, "gemini_api_key", ""):
            self.assertEqual(self.validate(GOOD_REPLY).json()["decision"], "PASS")
        self.gemini_cls.assert_not_called()

    def test_gemini_key_missing_and_groq_fails_returns_502(self):
        self.groq_create.side_effect = RuntimeError("boom")
        with mock.patch.object(settings, "gemini_api_key", ""):
            self.assertEqual(self.validate(GOOD_REPLY).status_code, 502)
        self.gemini_cls.assert_not_called()

    def test_both_fail_returns_clean_502(self):
        self.groq_create.side_effect = RuntimeError("key=secret groq details")
        self.gemini_generate.side_effect = RuntimeError("key=secret gemini details")
        response = self.validate(GOOD_REPLY)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret", response.text)
        self.assertIn("Could not validate", response.json()["detail"])
        self.groq_create.assert_called_once()
        self.gemini_generate.assert_called_once()

    def test_both_fail_with_quota_or_overload_returns_clear_503(self):
        self.groq_create.side_effect = _groq_rate_limit()
        for code, expected in {429: "usage limit", 503: "overloaded"}.items():
            with self.subTest(code=code):
                self.gemini_generate.side_effect = _gemini_error(code)
                response = self.validate(GOOD_REPLY)
                self.assertEqual(response.status_code, 503)
                self.assertIn(expected, response.json()["detail"])

    def test_no_provider_configured_returns_503_but_basic_checks_still_work(self):
        with mock.patch.object(settings, "groq_api_key", ""), \
                mock.patch.object(settings, "gemini_api_key", ""):
            response = self.validate(GOOD_REPLY)
            self.assertEqual(response.status_code, 503)
            self.assertIn("not configured", response.json()["detail"])
            self.assertEqual(self.validate("").json()["decision"], "FAIL")
        self.groq_cls.assert_not_called()
        self.gemini_cls.assert_not_called()

    # --- Verdict handling ---------------------------------------------------

    def test_fail_verdict_is_passed_through(self):
        self.groq_create.return_value = _groq_response(_verdict_json(
            "The reply refers to a restaurant meal.",
            review_relevance=False, business_relevance=False, safe_to_publish=False,
        ))
        body = self.validate("Thank you for visiting our restaurant!").json()
        self.assertEqual(body["decision"], "FAIL")
        self.assertFalse(body["passed"])
        self.assertEqual(body["reason"], "The reply refers to a restaurant meal.")
        self.assertFalse(body["checks"]["business_relevance"])
        self.assertTrue(body["checks"]["no_hallucination"])

    def test_any_failed_check_forces_fail_and_unsafe(self):
        # The model flags a hallucination but inconsistently leaves safe_to_publish true.
        self.groq_create.return_value = _groq_response(_verdict_json(None, no_hallucination=False))
        body = self.validate("Glad you used our 20% festival discount!").json()
        self.assertEqual(body["decision"], "FAIL")
        self.assertFalse(body["checks"]["safe_to_publish"])
        self.assertIn("no hallucination", body["reason"])

    # --- Cases that never reach an AI provider ----------------------------

    def test_ai_disclosure_fails_without_ai_call(self):
        for reply in ("As an AI, I am glad you liked the dress.",
                      "This AI-generated response thanks you for visiting."):
            with self.subTest(reply=reply):
                body = self.validate(reply).json()
                self.assertEqual(body["decision"], "FAIL")
                self.assertIn("AI", body["reason"])
        self.assert_no_ai_calls()

    def test_unfilled_placeholder_fails_without_ai_call(self):
        self.assertEqual(self.validate("Thank you [Customer Name]!").json()["decision"], "FAIL")
        self.assert_no_ai_calls()

    def test_empty_reply_fails_without_ai_call(self):
        for reply in ("", "   \n "):
            with self.subTest(reply=reply):
                response = self.validate(reply)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["decision"], "FAIL")
        self.assert_no_ai_calls()

    def test_over_byte_limit_fails_without_ai_call(self):
        body = self.validate("é" * (MAX_REPLY_BYTES // 2 + 1)).json()
        self.assertEqual(body["decision"], "FAIL")
        self.assertIn(str(MAX_REPLY_BYTES), body["reason"])
        self.assert_no_ai_calls()

    def test_already_replied_returns_409_without_ai_call(self):
        self.raw_review["reviewReply"] = {"comment": "Thanks!"}
        self.assertEqual(self.validate(GOOD_REPLY).status_code, 409)
        self.assert_no_ai_calls()


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
                self.assertIsNone(reply_validator.basic_reply_checks("r1", reply))


@unittest.skipUnless(
    os.getenv("RUN_LIVE_AI_TESTS") == "1" and (settings.groq_api_key or settings.gemini_api_key),
    "live AI tests disabled (set RUN_LIVE_AI_TESTS=1)",
)
class LiveJudgementTests(unittest.TestCase):
    """Real provider calls (Groq, Gemini only on Groq failure) per example case."""

    def check(self, rating: int, review: str, reply: str, expected: str):
        normalized = {"review_id": "live", "rating": rating, "reviewer": "Customer", "review": review}
        try:
            result = validate_review_reply(normalized, reply)
        except ReplyValidationError as exc:
            self.skipTest(f"AI provider unavailable: {exc}")
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
