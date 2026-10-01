"""Tests for reply generation with Groq primary / Gemini fallback.

These are UNIT tests: the Groq and Gemini SDK clients are replaced with mocks,
so nothing contacts either provider. They exercise the real provider modules,
the orchestrator, and the ``POST /reviews/{review_id}/generate`` endpoint.

Run with either:
    python -m unittest tests.test_reply_generation
    python -m pytest tests/test_reply_generation.py
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import groq
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai import prompts
from app.ai.reply_generator import ReplyGenerationError, generate_review_reply
from app.api import reviews as reviews_api
from app.config import settings

REVIEW = {"rating": 5, "reviewer": "Ann", "review": "Lovely fabric and fit!"}
RAW_REVIEW = {
    "reviewId": "r1",
    "reviewer": {"displayName": "Ann"},
    "starRating": "FIVE",
    "comment": "Lovely fabric and fit!",
    "createTime": "2026-09-01T10:00:00Z",
}


def _groq_response(content: str | None) -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _gemini_response(text: str | None, finish_reason: str = "STOP") -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        prompt_feedback=None,
        candidates=[SimpleNamespace(finish_reason=finish_reason)],
    )


def _groq_rate_limit_error() -> groq.RateLimitError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return groq.RateLimitError("Rate limit reached", response=response, body=None)


class FallbackTestCase(unittest.TestCase):
    """Patches both SDK clients and both API keys for every test."""

    def setUp(self):
        groq_patch = mock.patch("app.ai.groq_client.Groq")
        gemini_patch = mock.patch("app.ai.gemini_client.genai.Client")
        self.groq_cls = groq_patch.start()
        self.gemini_cls = gemini_patch.start()
        self.groq_create = self.groq_cls.return_value.chat.completions.create
        self.gemini_generate = self.gemini_cls.return_value.models.generate_content
        self.groq_create.return_value = _groq_response("Thank you from Groq!")
        self.gemini_generate.return_value = _gemini_response("Thank you from Gemini!")

        for name, value in (("groq_api_key", "gsk-test"), ("gemini_api_key", "gm-test")):
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(groq_patch.stop)
        self.addCleanup(gemini_patch.stop)


class ReplyGeneratorTests(FallbackTestCase):
    # Test 1
    def test_groq_success_does_not_call_gemini(self):
        reply = generate_review_reply(REVIEW)
        self.assertEqual(reply, "Thank you from Groq!")
        self.groq_create.assert_called_once()
        self.gemini_cls.assert_not_called()
        self.gemini_generate.assert_not_called()

    # Test 2
    def test_groq_failure_falls_back_to_gemini(self):
        self.groq_create.side_effect = RuntimeError("boom")
        reply = generate_review_reply(REVIEW)
        self.assertEqual(reply, "Thank you from Gemini!")
        self.groq_create.assert_called_once()
        self.gemini_generate.assert_called_once()

    # Test 3
    def test_both_providers_fail_raises_reply_generation_error(self):
        self.groq_create.side_effect = RuntimeError("groq down")
        self.gemini_generate.side_effect = RuntimeError("gemini down")
        with self.assertRaises(ReplyGenerationError):
            generate_review_reply(REVIEW)
        self.groq_create.assert_called_once()
        self.gemini_generate.assert_called_once()

    # Test 4
    def test_groq_rate_limit_falls_back_to_gemini(self):
        self.groq_create.side_effect = _groq_rate_limit_error()
        self.assertEqual(generate_review_reply(REVIEW), "Thank you from Gemini!")
        self.groq_create.assert_called_once()
        self.gemini_generate.assert_called_once()

    def test_groq_timeout_falls_back_to_gemini(self):
        request = httpx.Request("POST", "https://api.groq.com")
        self.groq_create.side_effect = groq.APITimeoutError(request=request)
        self.assertEqual(generate_review_reply(REVIEW), "Thank you from Gemini!")

    # Test 5
    def test_groq_key_missing_uses_gemini(self):
        with mock.patch.object(settings, "groq_api_key", ""):
            self.assertEqual(generate_review_reply(REVIEW), "Thank you from Gemini!")
        self.groq_cls.assert_not_called()
        self.gemini_generate.assert_called_once()

    # Test 6
    def test_gemini_key_missing_and_groq_succeeds(self):
        with mock.patch.object(settings, "gemini_api_key", ""):
            self.assertEqual(generate_review_reply(REVIEW), "Thank you from Groq!")
        self.gemini_cls.assert_not_called()

    def test_gemini_key_missing_and_groq_fails_reports_no_fallback(self):
        self.groq_create.side_effect = RuntimeError("groq down")
        with mock.patch.object(settings, "gemini_api_key", ""):
            with self.assertRaisesRegex(ReplyGenerationError, "no fallback"):
                generate_review_reply(REVIEW)
        self.gemini_cls.assert_not_called()

    # Test 7
    def test_empty_groq_reply_falls_back_to_gemini(self):
        for empty in ("", "   ", None):
            with self.subTest(empty=empty):
                self.groq_create.return_value = _groq_response(empty)
                self.assertEqual(generate_review_reply(REVIEW), "Thank you from Gemini!")

    # Test 8
    def test_empty_gemini_reply_is_a_failure(self):
        self.groq_create.side_effect = RuntimeError("groq down")
        self.gemini_generate.return_value = _gemini_response(None, "SAFETY")
        with self.assertRaises(ReplyGenerationError):
            generate_review_reply(REVIEW)

    def test_each_provider_is_attempted_once_without_sdk_retries(self):
        self.groq_create.side_effect = _groq_rate_limit_error()
        generate_review_reply(REVIEW)
        self.assertEqual(self.groq_cls.call_args.kwargs["max_retries"], 0)
        retry = self.gemini_cls.call_args.kwargs["http_options"].retry_options
        self.assertEqual(retry.attempts, 1)

    def test_logs_one_line_naming_groq_model_on_success(self):
        review = {**REVIEW, "review_id": "r1"}
        with self.assertLogs("app.ai.reply_generator") as logs:
            generate_review_reply(review)
        self.assertEqual(
            logs.output,
            [f"INFO:app.ai.reply_generator:Review r1: reply generated by Groq ({settings.groq_model})"],
        )

    def test_logs_groq_failure_reason_and_gemini_model_on_fallback(self):
        self.groq_create.side_effect = _groq_rate_limit_error()
        with self.assertLogs("app.ai.reply_generator") as logs:
            generate_review_reply({**REVIEW, "review_id": "r1"})
        self.assertEqual(len(logs.output), 2)
        self.assertIn("Groq", logs.output[0])
        self.assertIn("RateLimitError (HTTP 429)", logs.output[0])
        self.assertIn(f"reply generated by Gemini ({settings.gemini_model})", logs.output[1])

    def test_both_providers_receive_identical_instructions(self):
        self.groq_create.return_value = _groq_response("")  # force fallback
        generate_review_reply(REVIEW, tone="Apologetic", length="short")

        expected_user = prompts.build_user_prompt(REVIEW, tone="Apologetic", length="short")
        self.assertIn("Tone: sincerely apologetic", expected_user)
        groq_messages = self.groq_create.call_args.kwargs["messages"]
        self.assertEqual(groq_messages[0]["content"], prompts.SYSTEM_PROMPT)
        self.assertEqual(groq_messages[1]["content"], expected_user)

        gemini_kwargs = self.gemini_generate.call_args.kwargs
        self.assertEqual(gemini_kwargs["model"], settings.gemini_model)
        self.assertEqual(gemini_kwargs["contents"], expected_user)
        self.assertEqual(gemini_kwargs["config"].system_instruction, prompts.SYSTEM_PROMPT)


class GenerateEndpointTests(FallbackTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(reviews_api, "get_review", return_value=RAW_REVIEW)
        patcher.start()
        self.addCleanup(patcher.stop)
        app = FastAPI()
        app.include_router(reviews_api.router)
        self.client = TestClient(app)

    def test_response_contract_unchanged_on_gemini_fallback(self):
        self.groq_create.side_effect = _groq_rate_limit_error()
        response = self.client.post("/reviews/r1/generate")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"review_id", "reply", "review"})
        self.assertEqual(body["review_id"], "r1")
        self.assertEqual(body["reply"], "Thank you from Gemini!")
        self.assertEqual(body["review"]["rating"], 5)

    def test_both_fail_returns_clean_502(self):
        self.groq_create.side_effect = RuntimeError("secret-ish groq internals")
        self.gemini_generate.side_effect = RuntimeError("secret-ish gemini internals")
        response = self.client.post("/reviews/r1/generate")
        self.assertEqual(response.status_code, 502)
        detail = response.json()["detail"]
        self.assertIn("temporarily unavailable", detail)
        self.assertNotIn("internals", detail)
        self.assertNotIn("Groq", detail)
        self.assertNotIn("Gemini", detail)


if __name__ == "__main__":
    unittest.main()
