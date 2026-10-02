"""Automation endpoints: status, bulk backfill, reconciliation, and a development test trigger."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app.api.reviews import _to_http_error
from app.auth.google_oauth import GoogleOAuthError
from app.automation import backfill, reconcile
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
        "backfill_delay_seconds": settings.automation_backfill_delay_seconds,
        "verify_after_publish": settings.auto_reply_verify_after_publish,
        "reconciliation_enabled": settings.reconciliation_enabled,
        "reconciliation_auth_configured": settings.reconciliation_auth_configured,
        "reconciliation_max_reviews": settings.reconciliation_max_reviews,
        "reconciliation_lookback_minutes": settings.reconciliation_lookback_minutes,
        "last_reconciliation": (
            job.summary(include_items=False) if (job := reconcile.latest_job()) else None
        ),
    }


# --- Reconciliation (scheduled safety net) -------------------------------------------
def require_reconcile_secret(
    authorization: str | None = Header(None),
    x_reconcile_secret: str | None = Header(None),
) -> None:
    """Shared-secret check for the reconciliation endpoints (scheduler only).

    Accepts ``Authorization: Bearer <RECONCILIATION_SECRET>`` or
    ``X-Reconcile-Secret: <RECONCILIATION_SECRET>``. Closed (503) until a
    secret of at least 16 characters is configured. The secret is compared
    in constant time and never logged or echoed.
    """
    if not settings.reconciliation_auth_configured:
        raise HTTPException(
            status_code=503,
            detail="Reconciliation is not configured (set RECONCILIATION_SECRET, 16+ characters).",
        )
    provided = (x_reconcile_secret or "").strip()
    if not provided:
        scheme, _, token = (authorization or "").partition(" ")
        provided = token.strip() if scheme.lower() == "bearer" else ""
    if not provided:
        raise HTTPException(status_code=401, detail="Missing reconciliation secret.")
    expected = settings.reconciliation_secret.strip()
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid reconciliation secret.")


@router.post("/reconcile", dependencies=[Depends(require_reconcile_secret)])
async def start_reconciliation(
    location_id: str | None = Query(
        None, description="Business Profile location id. Defaults to the configured / first location."
    ),
    trigger: str = Query("scheduler", max_length=32, pattern=r"^[A-Za-z0-9_\-]+$"),
    wait: bool = Query(False, description="Wait (up to wait_timeout seconds) for the job to finish."),
    wait_timeout: float = Query(240, ge=1, le=900),
) -> JSONResponse:
    """Process recent unanswered reviews that Pub/Sub did not (yet) handle.

    Meant to be called by an external scheduler every few minutes (see
    README 13.11). Each review goes through the same ``process_new_review()``
    as the webhook. Returns ``202`` with the job while it runs (``200`` when
    nothing needed processing, or with ``wait=true``); ``409`` when a
    reconciliation or backfill is already running or automation is off.
    """
    try:
        job = await reconcile.start_reconciliation(location_id, trigger)
        if wait and job.active:
            job = await reconcile.wait_for_job(job.job_id, wait_timeout) or job
    except reconcile.ReconcileRejected as exc:
        return JSONResponse(
            {"started": False, "reason": exc.reason, "job_id": exc.job_id},
            status_code=exc.status_code,
        )
    except (GoogleReviewError, GoogleOAuthError, GoogleAPIError) as exc:
        raise _to_http_error(exc) from exc
    content = {"started": bool(job.items), **job.summary()}
    if not job.items:
        content["reason"] = "There are no recent unanswered reviews to reconcile."
    return JSONResponse(content, status_code=202 if job.active else 200)


@router.get("/reconcile", dependencies=[Depends(require_reconcile_secret)])
def latest_reconciliation() -> dict[str, Any]:
    """The running reconciliation, else the most recent one (``job`` is null if none)."""
    job = reconcile.latest_job()
    return {"job": job.summary() if job else None}


@router.get("/reconcile/{job_id}", dependencies=[Depends(require_reconcile_secret)])
def reconciliation_status(job_id: str) -> dict[str, Any]:
    """Progress of one reconciliation job (no reply text, no secrets)."""
    job = reconcile.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Reconciliation job not found. It may have finished before a server restart.",
        )
    return job.summary()


# --- Bulk backfill ----------------------------------------------------------------
@router.post("/backfill")
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


@router.post("/backfill/{job_id}/cancel")
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
    run = await process_new_review(review_id, location_id, allow_rerun=True, trigger="test")
    return run.summary()
