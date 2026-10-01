"""Pydantic models for the API contract.

Kept minimal: a normalized review, the AI generation response, the publish
request carrying the user-approved final text, the publish result, and the
manual reply-check (validation) request/result.
"""

from __future__ import annotations

from typing import Literal

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
    reply_comment: str | None = Field(
        default=None,
        description="The business reply text posted on Google, when present.",
    )
    reply_updated_at: str | None = Field(
        default=None,
        description="ISO-8601 timestamp of the business reply, when present.",
    )
    profile_photo_url: str | None = Field(
        default=None, description="Reviewer profile photo URL, when available."
    )


class LocationSummary(BaseModel):
    """A Business Profile location with per-location review counts."""

    location_id: str
    name: str
    address: str = ""
    total_reviews: int = 0
    answered: int = 0
    unanswered: int = 0
    average_rating: float | None = None


class ReviewStats(BaseModel):
    """Aggregate review statistics for the dashboard cards."""

    total_reviews: int = 0
    answered: int = 0
    unanswered: int = 0
    average_rating: float | None = None


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


class ValidateReplyRequest(BaseModel):
    """The draft reply to check via POST /reviews/{review_id}/validate.

    Empty or over-long text is accepted here on purpose: the validator reports
    it as a FAIL result instead of a request error.
    """

    reply: str = Field(description="The draft reply text currently shown to the user.")


class ValidationChecks(BaseModel):
    """Individual suitability checks; ``True`` means no clear problem found."""

    review_relevance: bool = Field(description="Reply fits this review and does not contradict it.")
    business_relevance: bool = Field(description="Reply fits a fashion boutique, not another business.")
    no_hallucination: bool = Field(description="No invented facts, offers, policies, names, or actions.")
    appropriate_tone: bool = Field(description="Polite and appropriate for the review's sentiment.")
    safe_to_publish: bool = Field(description="Nothing that makes the reply unsafe to post publicly.")


class ReplyValidationResult(BaseModel):
    """Result of POST /reviews/{review_id}/validate. Nothing is published."""

    review_id: str
    passed: bool
    decision: Literal["PASS", "FAIL"]
    reason: str | None = Field(
        default=None, description="Why the reply failed; null when it passed."
    )
    checks: ValidationChecks
