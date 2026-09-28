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
import re
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
# Resource-name construction
# ---------------------------------------------------------------------------
# The Business Information API (v1) returns a location's ``name`` as the bare
# ``locations/{location_id}`` (no account prefix). The Reviews API (v4),
# however, requires the full parent path
# ``accounts/{account_id}/locations/{location_id}``. These helpers bridge the
# two: they extract the location id from whatever form Google returned and
# always rebuild the canonical full resource name, so we never emit a v4 URL
# like ``/v4/locations/{id}/reviews`` (which 404s) or a duplicated path.
_ACCOUNT_RE = re.compile(r"^accounts/[^/]+$")


def _extract_location_id(value: str) -> str:
    """Return the bare ``{location_id}`` from any location reference.

    Accepts ``locations/{id}``, ``accounts/{acct}/locations/{id}``, or a bare
    ``{id}``. Returns ``""`` when no usable id can be found.
    """
    value = (value or "").strip().strip("/")
    if not value:
        return ""
    marker = "locations/"
    idx = value.rfind(marker)
    if idx != -1:
        location_id = value[idx + len(marker) :]
    elif "/" not in value:
        # A bare id (e.g. the value of GOOGLE_LOCATION_ID).
        location_id = value
    else:
        return ""
    location_id = location_id.strip("/")
    # A valid location id is a single path segment.
    if not location_id or "/" in location_id:
        return ""
    return location_id


def _build_review_location_resource(account_name: str, location_name: str) -> str:
    """Build the canonical ``accounts/{acct}/locations/{loc}`` resource.

    ``account_name`` comes from the Account Management API (authoritative);
    ``location_name`` is whatever the Business Information API returned. Raises
    LocationNotFoundError on malformed input rather than emitting a bad URL.
    """
    account = (account_name or "").strip().strip("/")
    if not _ACCOUNT_RE.match(account):
        raise LocationNotFoundError(
            f"Invalid Google account resource name: {account_name!r}. "
            "Expected the form 'accounts/{account_id}'."
        )
    location_id = _extract_location_id(location_name)
    if not location_id:
        raise LocationNotFoundError(
            f"Invalid Google location resource name: {location_name!r}. "
            "Expected 'locations/{id}' or "
            "'accounts/{acct}/locations/{id}'."
        )
    return f"{account}/locations/{location_id}"


# ---------------------------------------------------------------------------
# Location handling (single boutique location, Phase 1)
# ---------------------------------------------------------------------------
def get_location_name(
    client: GoogleBusinessClient | None = None,
    location_id: str | None = None,
) -> str:
    """Resolve the boutique's location name via the Business Profile APIs.

    The app targets one boutique location. Selection precedence:
    an explicit ``location_id`` argument (chosen in the UI) wins, then the
    ``GOOGLE_LOCATION_ID`` setting; otherwise the first location of the first
    My Business account is used. ``location_id`` may be a bare id or a full
    resource name.

    Always returns the canonical resource name the Reviews API (v4) needs:
    ``accounts/{account_id}/locations/{location_id}``. This is required
    because the Business Information API (v1) reports locations as the bare
    ``locations/{location_id}`` — passing that straight to the v4 Reviews URL
    produces ``/v4/locations/{id}/reviews`` and Google answers 404.
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
    logger.info("Google Business Profile account resource: %s", account_name)

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

    configured = (location_id or settings.google_location_id or "").strip()
    if configured:
        configured_id = _extract_location_id(configured)
        if not configured_id:
            raise LocationNotFoundError(
                f"GOOGLE_LOCATION_ID '{configured}' is not a valid location "
                "id or resource name."
            )
        for location in locations:
            raw_name = location.get("name") or ""
            if _extract_location_id(raw_name) == configured_id:
                resource = _build_review_location_resource(account_name, raw_name)
                logger.info(
                    "Google Business Profile location resource: %s "
                    "(pinned via GOOGLE_LOCATION_ID)",
                    resource,
                )
                return resource
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
            locations[0].get("name"),
        )
    resource = _build_review_location_resource(
        account_name, locations[0].get("name") or ""
    )
    logger.info("Google Business Profile location resource: %s", resource)
    return resource


# ---------------------------------------------------------------------------
# Review operations
# ---------------------------------------------------------------------------
def get_reviews(
    client: GoogleBusinessClient | None = None,
    location_id: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch all reviews of the boutique location (follows pagination)."""
    client = client or get_google_client()
    location_name = get_location_name(client, location_id)
    logger.info("Reviews API resource: %s/reviews", location_name)
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
    location_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return only reviews that do not yet have a business reply."""
    reviews = get_reviews(client, location_id)
    unanswered = [review for review in reviews if not has_reply(review)]
    logger.info("%d of %d reviews are unanswered", len(unanswered), len(reviews))
    return unanswered


def get_review(
    review_id: str,
    client: GoogleBusinessClient | None = None,
    location_id: str | None = None,
) -> dict[str, Any]:
    """Fetch the current version of one review from Google.

    ``review_id`` is the review's ``reviewId`` (last segment of the review
    resource name). Raises ReviewNotFoundError when Google returns 404.
    """
    client = client or get_google_client()
    location_name = get_location_name(client, location_id)
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
    location_id: str | None = None,
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
    location_name = get_location_name(client, location_id)
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
    reply = raw.get("reviewReply") or {}
    reply_comment = (reply.get("comment") or "").strip() or None
    return {
        "review_id": raw.get("reviewId") or "",
        "reviewer": reviewer_name,
        "rating": STAR_RATING_TO_INT.get(raw.get("starRating") or ""),
        "review": raw.get("comment") or "",
        "created_at": raw.get("createTime"),
        "has_reply": has_reply(raw),
        "reply_comment": reply_comment,
        "reply_updated_at": reply.get("updateTime"),
        "profile_photo_url": reviewer.get("profilePhotoUrl"),
    }


def _format_address(location: dict[str, Any]) -> str:
    """Build a human-readable address from a location's storefrontAddress."""
    address = location.get("storefrontAddress") or {}
    lines = list(address.get("addressLines") or [])
    parts = lines + [
        address.get("locality") or "",
        address.get("administrativeArea") or "",
        address.get("postalCode") or "",
    ]
    return ", ".join(part for part in parts if part).strip(", ")


def compute_stats(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute dashboard statistics from a list of raw Google reviews."""
    total = len(reviews)
    answered = sum(1 for review in reviews if has_reply(review))
    ratings = [
        STAR_RATING_TO_INT[review["starRating"]]
        for review in reviews
        if review.get("starRating") in STAR_RATING_TO_INT
    ]
    average = round(sum(ratings) / len(ratings), 1) if ratings else None
    return {
        "total_reviews": total,
        "answered": answered,
        "unanswered": total - answered,
        "average_rating": average,
    }


def list_business_locations(
    client: GoogleBusinessClient | None = None,
) -> list[dict[str, Any]]:
    """List the authorized account's Business Profile locations with counts.

    Each entry carries the location id, display name, formatted address and
    per-location review counts (total / answered / unanswered) plus the
    average rating, so the frontend can render the business selector and
    dashboard stats. Reviews are fetched per location (Phase 1 scale).
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
            "account."
        )
    account_name = accounts[0]["name"]

    try:
        locations_response = client.list_locations(account_name)
    except GoogleAPIError as exc:
        raise LocationNotFoundError(
            f"Could not list locations for {account_name}: {exc}"
        ) from exc
    locations = locations_response.get("locations") or []

    result: list[dict[str, Any]] = []
    for location in locations:
        location_id = _extract_location_id(location.get("name") or "")
        if not location_id:
            continue
        location_resource = f"{account_name}/locations/{location_id}"
        reviews: list[dict[str, Any]] = []
        page_token: str | None = None
        try:
            while True:
                response = client.list_reviews(
                    location_resource, REVIEW_PAGE_SIZE, page_token
                )
                reviews.extend(response.get("reviews") or [])
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
        except GoogleAPIError as exc:
            logger.warning(
                "Could not fetch reviews for %s: %s", location_resource, exc
            )
        stats = compute_stats(reviews)
        result.append(
            {
                "location_id": location_id,
                "name": location.get("title") or location_id,
                "address": _format_address(location),
                **stats,
            }
        )
    logger.info("Listed %d Business Profile locations", len(result))
    return result
