"""Automation endpoints: status, bulk backfill, and a development test trigger."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app.api.reviews import _to_http_error
from app.auth.google_oauth import GoogleOAuthError
from app.automation import backfill
from app.automation.processor import MAX_PROCESSING_ATTEMPTS, process_new_review
from app.config import settings
from app.google.client import GoogleAPIError
from app.google.reviews import GoogleReviewError
from app.webhooks.pubsub import pubsub_auth_configured

router = APIRouter(prefix="/automation", tags=["Automation"])


@router.get("/status")
def automation_status() -> dict[str, Any]:
    """Current automatic-reply configuration (no secrets)."""
    return {
        "enabled": settings.auto_reply_enabled,
        "dry_run": settings.auto_reply_dry_run,
        "max_regenerations": settings.auto_reply_max_regenerations,
        "max_processing_attempts": MAX_PROCESSING_ATTEMPTS,
        "location_ids": settings.auto_reply_location_list,
        "webhook_auth_configured": pubsub_auth_configured(),
        "test_endpoint_enabled": settings.automation_test_endpoint_enabled,
        "backfill_configured": bool(settings.automation_backfill_key),
        "backfill_delay_seconds": settings.automation_backfill_delay_seconds,
    }


# --- Bulk backfill ----------------------------------------------------------------
def require_backfill_key(
    x_automation_key: str | None = Header(None, description="AUTOMATION_BACKFILL_KEY"),
) -> None:
    """Shared admin key for starting / cancelling a backfill.

    The app has no user accounts and a backfill can publish many replies,
    so these endpoints fail closed: disabled while AUTOMATION_BACKFILL_KEY is
    empty, 403 for a missing or wrong key (401 is reserved for Google OAuth).
    """
    expected = settings.automation_backfill_key
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Bulk reply is not configured on the server (AUTOMATION_BACKFILL_KEY is not set).",
        )
    if not x_automation_key or not hmac.compare_digest(
        x_automation_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="The admin key is missing or incorrect.")


@router.post("/backfill", dependencies=[Depends(require_backfill_key)])
async def start_backfill(
    location_id: str | None = Query(
        None, description="Business Profile location id. Defaults to the configured / first location."
    ),
) -> JSONResponse:
    """Reply to every currently unanswered review, one at a time, in the background.

    Each review goes through the same pipeline as the Pub/Sub webhook
    (fresh Google check, generation, validation, final Google check, then
    publish — or WOULD_PUBLISH while AUTO_REPLY_DRY_RUN=true). Returns
    immediately; poll ``GET /automation/backfill/{job_id}`` for progress.
    Only one backfill runs at a time (409 otherwise).
    """
    try:
        job = await backfill.start_backfill(location_id)
    except backfill.BackfillRejected as exc:
        return JSONResponse(
            {"started": False, "reason": exc.reason, "job_id": exc.job_id},
            status_code=exc.status_code,
        )
    except (GoogleReviewError, GoogleOAuthError, GoogleAPIError) as exc:
        raise _to_http_error(exc) from exc
    content: dict[str, Any] = {
        "started": bool(job.items),
        "job_id": job.job_id,
        "location_id": job.location_id,
        "total_reviews": len(job.items),
        "dry_run": job.dry_run,
    }
    if not job.items:
        content["reason"] = "There are no pending reviews to reply to."
    return JSONResponse(content, status_code=202 if job.items else 200)


@router.get("/backfill")
def latest_backfill(
    location_id: str | None = Query(None, description="Only jobs for this location."),
) -> dict[str, Any]:
    """The running backfill, else the most recent one (``job`` is null if none)."""
    job = backfill.latest_job(location_id)
    return {"job": job.summary() if job else None}


@router.get("/backfill/{job_id}")
def backfill_status(job_id: str) -> dict[str, Any]:
    """Progress of one backfill job (no reply text, no secrets)."""
    job = backfill.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Backfill job not found. It may have finished before a server restart.",
        )
    return job.summary()


@router.post("/backfill/{job_id}/cancel", dependencies=[Depends(require_backfill_key)])
def cancel_backfill(job_id: str) -> dict[str, Any]:
    """Stop starting new reviews; a review already in progress finishes normally."""
    job = backfill.cancel_backfill(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Backfill job not found.")
    return job.summary()


@router.post(
    "/test/{review_id}",
    include_in_schema=settings.automation_test_endpoint_enabled,
)
async def automation_test(
    review_id: str,
    location_id: str | None = Query(None, description="Business Profile location id."),
) -> dict[str, Any]:
    """DEVELOPMENT ONLY: run the production automation for an existing review.

    Disabled (404) unless AUTOMATION_TEST_ENDPOINT_ENABLED=true. Calls the
    same :func:`process_new_review` as the Pub/Sub webhook, so
    AUTO_REPLY_ENABLED, AUTO_REPLY_DRY_RUN and both Google reply checks all
    apply. It only skips the "recently finished" duplicate memory so the
    same review can be re-tested.
    """
    if not settings.automation_test_endpoint_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    run = await process_new_review(review_id, location_id, allow_rerun=True)
    return run.summary()
