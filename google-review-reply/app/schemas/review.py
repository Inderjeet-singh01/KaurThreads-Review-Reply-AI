"""Pydantic models for the API contract.

Kept minimal: a normalized review, the AI generation response, the publish
request carrying the user-approved final text, and the publish result.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# Google's maximum length for a review reply (bytes).
MAX_REPLY_BYTES = 4096


class Review(BaseModel):
    """A Google review, normalized for this application."""

    review_id: str = Field(description="Google review ID.")
    reviewer: str = Field(description="Reviewer display name ('Anonymous' when hidden).")
    rating: int | None = Field(
        default=None, description="Star rating 1-5, or null when not rated."
    )
    review: str = Field(default="", description="Review text (empty for rating-only reviews).")
    created_at: str | None = Field(
        default=None, description="ISO-8601 timestamp of when the review was written."
    )
    has_reply: bool = Field(
        default=False,
        description="Whether the business has already replied on Google.",
    )


class GenerateReplyResponse(BaseModel):
    """Result of POST /reviews/{review_id}/generate.

    The reply is only a draft: it is NOT published. The user may edit it and
    publish it later via POST /reviews/{review_id}/publish.
    """

    review_id: str
    reply: str = Field(
        description=(
            "AI-generated reply draft. Edit freely; nothing is published "
            "until you explicitly call the publish endpoint."
        )
    )
    review: Review


class PublishRequest(BaseModel):
    """The final, user-approved reply text for POST /reviews/{review_id}/publish."""

    reply: str = Field(
        min_length=1,
        max_length=MAX_REPLY_BYTES,
        description="Final reply text, written or edited by the user.",
    )

    @field_validator("reply")
    @classmethod
    def _validate_reply_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reply must contain non-whitespace text")
        if len(stripped.encode("utf-8")) > MAX_REPLY_BYTES:
            raise ValueError(f"reply must be at most {MAX_REPLY_BYTES} bytes")
        return stripped


class PublishResult(BaseModel):
    """Outcome of a publish request."""

    review_id: str
    published: bool = Field(description="True only when the reply was published to Google.")
    message: str
    reply: str | None = Field(
        default=None, description="The exact text published to Google."
    )
