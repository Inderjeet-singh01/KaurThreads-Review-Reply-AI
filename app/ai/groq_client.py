"""Groq integration: generate a professional boutique reply for a review.

This module's only job is: "Generate a reply with Groq." It never talks to
Google, never decides anything about review state, never publishes anything,
and knows nothing about fallback providers (see :mod:`app.ai.reply_generator`).
"""

from __future__ import annotations

from typing import Any

from groq import Groq

from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from app.config import settings

# One attempt per request: on failure the orchestrator falls back to Gemini
# instead of letting the SDK retry (its default is 2 retries with backoff).
_MAX_RETRIES = 0
_TIMEOUT_SECONDS = 30.0


class GroqError(Exception):
    """Groq reply generation failed or Groq is not configured."""


def _describe(exc: Exception) -> str:
    """Short, secret-free description of an SDK exception for logs/errors."""
    status = getattr(exc, "status_code", None)
    return f"{type(exc).__name__} (HTTP {status})" if status else type(exc).__name__


def generate_review_reply_groq(
    review: dict[str, Any],
    tone: str | None = None,
    length: str | None = None,
    revision_feedback: str | None = None,
) -> str:
    """Generate a professional reply for a normalized review using Groq.

    See :func:`app.ai.prompts.build_user_prompt` for the ``review``, ``tone``
    and ``length`` contract. Returns the generated reply text and raises
    :class:`GroqError` on any failure (not configured, API/network error,
    rate limit, timeout, or empty response). Never publishes anything.
    """
    if not settings.groq_api_key:
        raise GroqError("GROQ_API_KEY not configured")

    user_prompt = build_user_prompt(
        review, tone=tone, length=length, revision_feedback=revision_feedback
    )

    try:
        client = Groq(
            api_key=settings.groq_api_key,
            max_retries=_MAX_RETRIES,
            timeout=_TIMEOUT_SECONDS,
        )
        response = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=250,
        )
        reply = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        raise GroqError(_describe(exc)) from exc

    if not reply:
        raise GroqError("empty reply")
    return reply
