"""Reply generation with provider fallback: Groq primary, Gemini fallback.

Flow (one attempt per provider, no retry loops):

    Groq ── success ──> return reply          (Gemini is never called)
      │
    failure (not configured, API error, 429, timeout, empty reply, ...)
      │
    Gemini ── success ──> return reply
      │
    failure / not configured ──> ReplyGenerationError

Provider-specific exceptions stay inside the AI layer; callers only ever see
:class:`ReplyGenerationError`, whose message is safe to show to end users.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai.gemini_client import GeminiError, generate_review_reply_gemini
from app.ai.groq_client import GroqError, generate_review_reply_groq
from app.config import settings

logger = logging.getLogger(__name__)


class ReplyGenerationError(Exception):
    """Every available AI provider failed; the message is user-safe."""


def generate_review_reply(
    review: dict[str, Any],
    tone: str | None = None,
    length: str | None = None,
) -> str:
    """Generate a reply draft with Groq, falling back to Gemini on failure.

    Same arguments as the provider functions (see
    :func:`app.ai.prompts.build_user_prompt`). Returns the reply text or
    raises :class:`ReplyGenerationError`. Never publishes anything.
    """
    logger.info("Starting reply generation using Groq")
    try:
        reply = generate_review_reply_groq(review, tone=tone, length=length)
    except GroqError as groq_error:
        logger.warning("Groq generation failed: %s", groq_error)
    else:
        logger.info("Groq generation succeeded")
        return reply

    if not settings.gemini_api_key:
        logger.error("Groq generation failed and no Gemini fallback is configured")
        raise ReplyGenerationError(
            "Reply generation failed and no fallback AI provider is "
            "configured. Please try again later."
        )

    logger.info("Attempting Gemini fallback")
    try:
        reply = generate_review_reply_gemini(review, tone=tone, length=length)
    except GeminiError as gemini_error:
        logger.error(
            "Groq and Gemini generation both failed. Gemini: %s", gemini_error
        )
        raise ReplyGenerationError(
            "Reply generation is temporarily unavailable. Please try again "
            "in a moment."
        ) from gemini_error
    logger.info("Gemini fallback generation succeeded")
    return reply
