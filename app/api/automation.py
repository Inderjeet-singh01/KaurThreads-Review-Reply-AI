"""Automation endpoints: read-only status and a development test trigger."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.automation.processor import MAX_PROCESSING_ATTEMPTS, process_new_review
from app.config import settings
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
    }


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
