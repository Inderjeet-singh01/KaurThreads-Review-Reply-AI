"""API endpoint for listing Business Profile locations.

Powers the "Select Business Location" screen and the "Change business"
action. Each location carries per-location review counts so the UI can show
the total / unanswered figures next to each business.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.auth.google_oauth import (
    GoogleOAuthError,
    GoogleTokenRefreshUnavailableError,
    GoogleTokenStoreError,
)
from app.google.client import GoogleAPIError
from app.google.reviews import (
    GoogleReviewError,
    LocationNotFoundError,
    list_business_locations,
)
from app.schemas.review import LocationSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/locations", tags=["Locations"])


def _to_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (GoogleTokenStoreError, GoogleTokenRefreshUnavailableError)):
        # Database or Google temporarily unavailable: not a reason to re-authenticate.
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, GoogleOAuthError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, LocationNotFoundError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, GoogleAPIError):
        if exc.status == 429:
            return HTTPException(status_code=503, detail=str(exc))
        if exc.status == 403:
            return HTTPException(status_code=403, detail=str(exc))
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, GoogleReviewError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail="Unexpected server error")


@router.get("", response_model=list[LocationSummary])
def list_locations() -> list[LocationSummary]:
    """List the authorized account's Business Profile locations with counts."""
    try:
        locations = list_business_locations()
    except (GoogleReviewError, GoogleOAuthError, GoogleAPIError) as exc:
        raise _to_http_error(exc) from exc
    return [LocationSummary(**location) for location in locations]
