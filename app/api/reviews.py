"""API endpoints for reviews, reply generation, and publishing.

Handlers are thin: Google operations come from :mod:`app.google.reviews` and
reply generation from :mod:`app.ai.groq_client`.

Publishing is only ever triggered by an explicit call to
``POST /reviews/{review_id}/publish`` with the user-approved final text, and
only after a final check that the review still has no business reply on
Google. Generation never publishes anything.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.ai.groq_client import GroqError, generate_review_reply
from app.auth.google_oauth import GoogleOAuthError
from app.google.client import GoogleAPIError
from app.google.reviews import (
    GoogleReviewError,
    LocationNotFoundError,
    ReviewNotFoundError,
    get_review,
    get_unanswered_reviews,
    has_reply,
    normalize_review,
    publish_reply,
)
from app.schemas.review import (
    GenerateReplyResponse,
    PublishRequest,
    PublishResult,
    Review,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews", tags=["Reviews"])


def _to_http_error(exc: Exception) -> HTTPException:
    """Map domain exceptions to clean FastAPI HTTP errors."""
    if isinstance(exc, GoogleOAuthError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, LocationNotFoundError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ReviewNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, GoogleAPIError):
        # Surface Google's status when it distinguishes the failure mode.
        if exc.status == 429:
            return HTTPException(status_code=503, detail=str(exc))
        if exc.status == 403:
            return HTTPException(status_code=403, detail=str(exc))
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, GoogleReviewError):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, GroqError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail="Unexpected server error")


@router.get("", response_model=list[Review])
def list_unanswered_reviews() -> list[Review]:
    """Return only reviews that do NOT yet have a business reply.

    Reviews that already carry a business reply on Google are skipped;
    Google is the source of truth for reply state.
    """
    try:
        unanswered = get_unanswered_reviews()
    except (GoogleReviewError, GoogleOAuthError, GoogleAPIError) as exc:
        raise _to_http_error(exc) from exc
    return [Review(**normalize_review(review)) for review in unanswered]


@router.post("/{review_id}/generate", response_model=GenerateReplyResponse)
def generate_reply(review_id: str) -> GenerateReplyResponse:
    """Generate an AI reply draft for a review.

    Fetches the current review from Google, refuses to run when the review
    already has a business reply, and returns the generated text as a draft.
    Nothing is published by this endpoint.
    """
    try:
        raw_review = get_review(review_id)
    except (GoogleReviewError, GoogleOAuthError, GoogleAPIError) as exc:
        raise _to_http_error(exc) from exc

    if has_reply(raw_review):
        logger.info(
            "Generation blocked for review %s: a reply already exists on Google",
            review_id,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "This review already has a business reply on Google, so no "
                "new reply was generated."
            ),
        )

    review = normalize_review(raw_review)
    logger.info("Reply generation requested for review %s", review_id)
    try:
        reply = generate_review_reply(review)
    except GroqError as exc:
        raise _to_http_error(exc) from exc
    logger.info("Reply generated for review %s (not published)", review_id)
    return GenerateReplyResponse(
        review_id=review["review_id"],
        reply=reply,
        review=Review(**review),
    )


@router.post("/{review_id}/publish", response_model=PublishResult)
def publish_review_reply(review_id: str, payload: PublishRequest) -> PublishResult:
    """Publish the user-approved final reply to Google.

    The review is re-fetched from Google immediately before publishing. If a
    business reply now exists (for example someone else answered in the
    meantime), publishing is blocked and nothing is sent to Google.
    """
    final_text = payload.reply
    logger.info(
        "Publish requested for review %s (%d characters)", review_id, len(final_text)
    )

    try:
        raw_review = get_review(review_id)
    except (GoogleReviewError, GoogleOAuthError, GoogleAPIError) as exc:
        raise _to_http_error(exc) from exc

    if has_reply(raw_review):
        logger.warning(
            "Publish blocked for review %s: a reply already exists on Google",
            review_id,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "This review has already been replied to on Google. The "
                "reply was NOT published."
            ),
        )

    try:
        publish_reply(review_id, final_text)
    except (GoogleReviewError, GoogleOAuthError, GoogleAPIError) as exc:
        raise _to_http_error(exc) from exc

    logger.info("Reply published for review %s", review_id)
    return PublishResult(
        review_id=review_id,
        published=True,
        message="Reply published to Google.",
        reply=final_text,
    )
