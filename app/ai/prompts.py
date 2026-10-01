"""Shared reply-generation instructions — the single source of truth.

Every AI provider (Groq, Gemini) builds its request from this module, so all
providers follow exactly the same business rules and receive exactly the same
review information.
"""

from __future__ import annotations

from typing import Any

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


# Human-readable guidance appended to the prompt for the optional UI controls.
_TONE_GUIDANCE = {
    "friendly & professional": "Tone: friendly and professional.",
    "warm & personal": "Tone: warm, personal and heartfelt.",
    "professional": "Tone: polished and strictly professional.",
    "apologetic": (
        "Tone: sincerely apologetic and understanding (do not admit legal "
        "fault or promise compensation)."
    ),
}
_LENGTH_GUIDANCE = {
    "short": "Length: very concise, 1 to 2 short sentences.",
    "medium": "Length: 2 to 3 short sentences.",
    "long": "Length: 4 to 5 short sentences, still concise.",
}


def build_user_prompt(
    review: dict[str, Any],
    tone: str | None = None,
    length: str | None = None,
) -> str:
    """Build the per-review user prompt sent alongside :data:`SYSTEM_PROMPT`.

    ``review`` is the normalized review shape produced by
    :func:`app.google.reviews.normalize_review` (``rating``, ``reviewer``,
    ``review`` — the text may be empty for rating-only reviews). ``tone`` and
    ``length`` are optional UI hints that nudge the wording; unknown values
    are ignored so the endpoint stays permissive.
    """
    rating = review.get("rating")
    reviewer = review.get("reviewer") or "a customer"
    text = (review.get("review") or "").strip()
    review_text = (
        text
        if text
        else "(The customer left no written comment - rating only.)"
    )
    rating_line = f"{rating} out of 5" if rating is not None else "not rated"

    preferences = [
        guidance
        for value, table in (
            (tone, _TONE_GUIDANCE),
            (length, _LENGTH_GUIDANCE),
        )
        if value and (guidance := table.get(value.strip().lower()))
    ]
    preference_block = (
        ("\nReply preferences:\n" + "\n".join(preferences) + "\n")
        if preferences
        else ""
    )

    return (
        "Customer review for our boutique:\n"
        f"Rating: {rating_line}\n"
        f"Reviewer: {reviewer}\n"
        f"Review text: {review_text}\n"
        f"{preference_block}\n"
        "Write our public reply to this review now. Return only the reply text."
    )
