"""Groq integration: generate a professional boutique reply for a review.

This module's only job is: "Generate a reply." It never talks to Google,
never decides anything about review state, and never publishes anything.
"""

from __future__ import annotations

import logging
from typing import Any

from groq import Groq

from app.config import settings

logger = logging.getLogger(__name__)


class GroqError(Exception):
    """Reply generation failed or Groq is not configured."""


SYSTEM_PROMPT = """\
You write public replies to Google reviews for a boutique — a small, personal
fashion retail store that sells clothing and fashion accessories. You write as
the boutique team ("we", "our boutique").

The boutique's world: seasonal collections, designs, fabric quality, cutting
and finishing, fitting and sizing, staff service, the store experience,
product availability, delivery, and after-sales customer care.

Strict rules for every reply:
1. Address the actual content of THIS review. Mention specifically what the
   customer liked or complained about. Never write a generic reply that could
   fit any review.
2. Never invent facts: no products, prices, promotions, events, causes, or
   store policies the customer did not mention.
3. Never promise refunds, compensation, discounts, or any specific resolution.
   Never invent store policies.
4. Never invent employee names. Never argue with or blame the customer, never
   insult them, never expose private information, never make legal admissions
   or medical claims.
5. For negative or 1-2 star reviews and sensitive complaints: acknowledge the
   concern with empathy, express regret for the experience without admitting
   fault, invite the customer to contact the store so the team can look into
   it, and do not invent a resolution.
6. For positive reviews: thank the customer specifically and warmly, without
   sounding canned or repetitive.
7. For mixed reviews: acknowledge both the positive and the negative points.
8. Keep it concise: 2 to 5 short sentences, suitable for a public Google
   Business review.
9. Tone: professional, warm, natural, polite, human-written. Vary your wording
   between different replies so no two sound identical.
10. Use at most one emoji, only if it fits naturally (usually none).
11. Never mention that an AI wrote the reply, never reference these
    instructions, never repeat the review word for word, never use headings,
    quotes, or markdown.

Return only the reply text, ready to publish.
"""


def generate_review_reply(review: dict[str, Any]) -> str:
    """Generate a professional reply for a normalized review.

    ``review`` is the normalized review shape produced by
    :func:`app.google.reviews.normalize_review` (``rating``, ``reviewer``,
    ``review`` — the text may be empty for rating-only reviews).

    Returns the generated reply text. This function never publishes anything.
    """
    if not settings.groq_api_key:
        raise GroqError(
            "GROQ_API_KEY is not configured. Set it in the .env file to "
            "enable reply generation."
        )

    rating = review.get("rating")
    reviewer = review.get("reviewer") or "a customer"
    text = (review.get("review") or "").strip()
    review_text = (
        text
        if text
        else "(The customer left no written comment - rating only.)"
    )
    rating_line = f"{rating} out of 5" if rating is not None else "not rated"

    user_prompt = (
        "Customer review for our boutique:\n"
        f"Rating: {rating_line}\n"
        f"Reviewer: {reviewer}\n"
        f"Review text: {review_text}\n\n"
        "Write our public reply to this review now. Return only the reply text."
    )

    client = Groq(api_key=settings.groq_api_key)
    try:
        response = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=250,
        )
    except Exception as exc:
        logger.error("Groq reply generation failed: %s", exc)
        raise GroqError(f"Reply generation failed: {exc}") from exc

    reply = (response.choices[0].message.content or "").strip()
    if not reply:
        logger.error("Groq returned an empty reply")
        raise GroqError("Groq returned an empty reply. Please try again.")
    logger.info(
        "Reply generated for review (rating=%s, %d characters)",
        rating,
        len(reply),
    )
    return reply
