"""Google Business Profile review operations for the boutique.

All review-related Google API calls and review business logic live here:

* resolving the boutique's location through the Business Profile APIs,
* fetching reviews (with pagination),
* deciding which reviews the business has already answered,
* fetching a single review,
* the final reply-existence check before publishing,
* publishing the user-approved reply.

Google is the source of truth for whether a review already has a business
reply: nothing is stored or cached locally, every call reads the latest
Google state.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.google.client import (
    GoogleAPIError,
    GoogleBusinessClient,
    get_google_client,
)

logger = logging.getLogger(__name__)

# Google's StarRating enum values -> simple numeric rating.
STAR_RATING_TO_INT = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}

REVIEW_PAGE_SIZE = 50  # maximum page size accepted by the Reviews API
MAX_REPLY_BYTES = 4096  # maximum reply length accepted by the Reviews API


class GoogleReviewError(Exception):
    """Base class for review-related errors."""


class LocationNotFoundError(GoogleReviewError):
    """No usable My Business account/location for the authorized account."""


class ReviewNotFoundError(GoogleReviewError):
    """The review does not exist (or was deleted)."""


# ---------------------------------------------------------------------------
# Location handling (single boutique location, Phase 1)
# ---------------------------------------------------------------------------
def get_location_name(client: GoogleBusinessClient | None = None) -> str:
    """Resolve the boutique's location name via the Business Profile APIs.

    The app targets one boutique location. When ``GOOGLE_LOCATION_ID`` is
    set, that location is selected (by location ID or full resource name);
    otherwise the first location of the first My Business account is used.

    Returns a resource name of the form
    ``accounts/{account_id}/locations/{location_id}``.
    """
    client = client or get_google_client()

    try:
        accounts_response = client.list_accounts()
    except GoogleAPIError as exc:
        raise LocationNotFoundError(
            f"Could not list My Business accounts: {exc}"
        ) from exc

    accounts = accounts_response.get("accounts") or []
    if not accounts:
        raise LocationNotFoundError(
            "No My Business account was found for the authorized Google "
            "account. Verify that this Google account owns or manages the "
            "boutique's Business Profile and that the Google Business "
            "Profile APIs are enabled for your project."
        )
    if len(accounts) > 1:
        logger.warning(
            "Multiple My Business accounts are accessible; using %s",
            accounts[0].get("name"),
        )
    account_name = accounts[0]["name"]

    try:
        locations_response = client.list_locations(account_name)
    except GoogleAPIError as exc:
        raise LocationNotFoundError(
            f"Could not list locations for {account_name}: {exc}"
        ) from exc

    locations = locations_response.get("locations") or []
    if not locations:
        raise LocationNotFoundError(
            f"No location found in My Business account {account_name}. "
            "Verify the Business Profile exists and this Google account "
            "can manage it."
        )

    configured = settings.google_location_id.strip()
    if configured:
        for location in locations:
            name = location.get("name") or ""
            if name == configured or name.endswith(f"locations/{configured}"):
                logger.info("Using configured location %s", name)
                return name
        raise LocationNotFoundError(
            f"Location '{configured}' (GOOGLE_LOCATION_ID) was not found in "
            f"account {account_name}. Check the configured value."
        )

    if len(locations) > 1:
        titles = ", ".join(
            location.get("title") or location.get("name", "?")
            for location in locations
        )
        logger.warning(
            "Multiple locations found (%s); using the first: %s. Set "
            "GOOGLE_LOCATION_ID to pin a specific one.",
            titles,
            locations[0]["name"],
        )
    location_name = locations[0]["name"]
    logger.info("Using business location %s", location_name)
    return location_name


# ---------------------------------------------------------------------------
# Review operations
# ---------------------------------------------------------------------------
def get_reviews(client: GoogleBusinessClient | None = None) -> list[dict[str, Any]]:
    """Fetch all reviews of the boutique location (follows pagination)."""
    client = client or get_google_client()
    location_name = get_location_name(client)
    reviews: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        response = client.list_reviews(location_name, REVIEW_PAGE_SIZE, page_token)
        reviews.extend(response.get("reviews") or [])
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    logger.info("Fetched %d reviews for location %s", len(reviews), location_name)
    return reviews


def has_reply(review: dict[str, Any]) -> bool:
    """Whether Google shows a business reply for this review.

    A review counts as answered when its ``reviewReply`` field contains a
    non-empty comment (regardless of the reply's moderation state).
    """
    reply = review.get("reviewReply") or {}
    return bool((reply.get("comment") or "").strip())


def get_unanswered_reviews(
    client: GoogleBusinessClient | None = None,
) -> list[dict[str, Any]]:
    """Return only reviews that do not yet have a business reply."""
    reviews = get_reviews(client)
    unanswered = [review for review in reviews if not has_reply(review)]
    logger.info("%d of %d reviews are unanswered", len(unanswered), len(reviews))
    return unanswered


def get_review(
    review_id: str, client: GoogleBusinessClient | None = None
) -> dict[str, Any]:
    """Fetch the current version of one review from Google.

    ``review_id`` is the review's ``reviewId`` (last segment of the review
    resource name). Raises ReviewNotFoundError when Google returns 404.
    """
    client = client or get_google_client()
    location_name = get_location_name(client)
    review_name = f"{location_name}/reviews/{review_id}"
    try:
        return client.get_review(review_name)
    except GoogleAPIError as exc:
        if exc.status == 404:
            raise ReviewNotFoundError(
                f"Review {review_id} was not found on Google."
            ) from exc
        raise


def publish_reply(
    review_id: str,
    comment: str,
    client: GoogleBusinessClient | None = None,
) -> dict[str, Any]:
    """Publish a business reply to a review (Reviews API ``updateReply``).

    The caller is responsible for the final reply-existence check; this
    function only performs the publish. Returns the ReviewReply object
    Google stored.
    """
    client = client or get_google_client()
    if len(comment.encode("utf-8")) > MAX_REPLY_BYTES:
        raise GoogleReviewError(
            f"Reply is too long for Google (max {MAX_REPLY_BYTES} bytes)."
        )
    location_name = get_location_name(client)
    review_name = f"{location_name}/reviews/{review_id}"
    try:
        response = client.update_review_reply(review_name, comment)
    except GoogleAPIError as exc:
        raise GoogleAPIError(
            f"Publishing the reply to Google failed: {exc}", status=exc.status
        ) from exc
    logger.info("Reply published to review %s", review_id)
    return response


def normalize_review(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw Google Review object into the flat shape the API exposes.

    The star-rating enum is normalized to a number (FIVE -> 5, ...);
    anonymous reviewers are reported as "Anonymous".
    """
    reviewer = raw.get("reviewer") or {}
    if reviewer.get("isAnonymous"):
        reviewer_name = "Anonymous"
    else:
        reviewer_name = reviewer.get("displayName") or "Anonymous"
    return {
        "review_id": raw.get("reviewId") or "",
        "reviewer": reviewer_name,
        "rating": STAR_RATING_TO_INT.get(raw.get("starRating") or ""),
        "review": raw.get("comment") or "",
        "created_at": raw.get("createTime"),
        "has_reply": has_reply(raw),
    }
