"""Gemini integration: fallback reply generation for a review.

This module's only job is: "Generate a reply with Gemini." It uses the same
shared prompt as Groq (:mod:`app.ai.prompts`), so replies follow identical
business rules whichever provider produced them. It never talks to Google
Business Profile and never publishes anything.
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import types

from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from app.config import settings

# Single attempt, no SDK-level retries: this is already the fallback path.
_TIMEOUT_MS = 30_000
# Gemini counts thinking tokens against max_output_tokens, so leave headroom
# above Groq's 250; the prompt itself keeps the visible reply short.
_MAX_OUTPUT_TOKENS = 1024


class GeminiError(Exception):
    """Gemini reply generation failed or Gemini is not configured."""


def _describe(exc: Exception) -> str:
    """Short, secret-free description of an SDK exception for logs/errors."""
    code = getattr(exc, "code", None)
    return f"{type(exc).__name__} (HTTP {code})" if code else type(exc).__name__


def _empty_reason(response: types.GenerateContentResponse) -> str:
    """Why Gemini returned no text (safety block, token limit, ...)."""
    feedback = response.prompt_feedback
    if feedback is not None and feedback.block_reason:
        return f"prompt blocked: {feedback.block_reason}"
    if response.candidates:
        return f"finish reason: {response.candidates[0].finish_reason}"
    return "no candidates"


def generate_review_reply_gemini(
    review: dict[str, Any],
    tone: str | None = None,
    length: str | None = None,
    revision_feedback: str | None = None,
) -> str:
    """Generate a professional reply for a normalized review using Gemini.

    Same contract as :func:`app.ai.groq_client.generate_review_reply_groq`.
    Raises :class:`GeminiError` on any failure (not configured, API/network
    error, rate limit, timeout, blocked or empty response).
    """
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY not configured")

    user_prompt = build_user_prompt(
        review, tone=tone, length=length, revision_feedback=revision_feedback
    )

    try:
        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(
                timeout=_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW
                ),
                # No tools are used; disabling AFC also silences an SDK warning.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
        reply = (response.text or "").strip()
    except Exception as exc:
        raise GeminiError(_describe(exc)) from exc

    if not reply:
        raise GeminiError(f"empty reply ({_empty_reason(response)})")
    return reply
