"""POST /webhooks/google-reviews — Business Profile new-review notifications.

Google Business Profile publishes a NEW_REVIEW notification to a Cloud
Pub/Sub topic; an authenticated push subscription delivers it here. This
handler stays thin: authenticate, decode, hand the notification to
:func:`app.automation.resolver.process_review_notification` (exact review,
or fallback resolution from Google when the review reference is unusable),
which runs the shared :func:`app.automation.processor.process_new_review`,
and translate the outcome into Pub/Sub acknowledgement semantics:

* 2xx  — acknowledged: processed, including "validation rejected the
  reply", "already replied", "no unanswered review found", "automation
  disabled" and ignored events. These must not be redelivered.
* 409  — the same review is being processed right now; redeliver later.
* 503  — transient Google / AI infrastructure failure; redeliver (bounded
  by the processor's retry budget and the subscription's retry policy).
* 400  — undecodable message, or a NEW_REVIEW without any valid location
  (redelivery cannot fix it; a dead-letter topic collects these).
* 401 / 403 — not an authenticated Pub/Sub push; nothing is processed.

Every request is traced with ``WEBHOOK_RECEIVED`` / ``WEBHOOK_REJECTED`` /
``WEBHOOK_RESULT`` log lines (message id, raw and normalized review and
location values, stage, final status). Tokens and headers are never logged.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.automation.processor import AutomationRun, AutomationStatus
from app.automation.resolver import process_review_notification
from app.webhooks.pubsub import (
    MalformedPushError,
    PubSubAuthError,
    PubSubAuthNotConfiguredError,
    PubSubMissingTokenError,
    parse_push_body,
    verify_push_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/google-reviews")
async def google_reviews_webhook(request: Request) -> JSONResponse:
    """Receive an authenticated Pub/Sub push for a Business Profile review."""
    try:
        # Signature verification fetches Google's certificates (blocking I/O).
        await run_in_threadpool(verify_push_token, request.headers.get("authorization"))
    except PubSubAuthNotConfiguredError:
        logger.error(
            "Pub/Sub push rejected: PUBSUB_PUSH_AUDIENCE / "
            "PUBSUB_PUSH_SERVICE_ACCOUNT are not configured"
        )
        raise HTTPException(status_code=503, detail="Webhook authentication is not configured.")
    except PubSubAuthError as exc:
        # The reason is generic by construction; the token is never logged.
        logger.warning("Pub/Sub push rejected: %s", exc)
        status = 401 if isinstance(exc, PubSubMissingTokenError) else 403
        raise HTTPException(status_code=status, detail="Unauthorized push request.")

    try:
        body: Any = await request.json()
    except ValueError:
        logger.warning("WEBHOOK_REJECTED stage=PARSING reason=body is not JSON")
        raise HTTPException(status_code=400, detail="Body must be a Pub/Sub push JSON envelope.")
    try:
        event = parse_push_body(body)
    except MalformedPushError as exc:
        # Kept for log searches: "Pub/Sub push rejected".
        logger.warning(
            "WEBHOOK_REJECTED message_id=%s stage=PARSING error_type=malformed "
            "Pub/Sub push rejected: %s",
            exc.message_id or "-", exc,
        )
        raise HTTPException(status_code=400, detail="Malformed Pub/Sub message.")

    stage = "PARSED" if event.review_id else "FALLBACK_RESOLUTION"
    logger.info(
        "WEBHOOK_RECEIVED message_id=%s delivery_attempt=%s publish_time=%s event=%s "
        "location_raw=%s review_raw=%s normalized_location=%s normalized_review=%s "
        "review_id=%s stage=%s%s",
        event.message_id or "-", event.delivery_attempt, event.publish_time or "-",
        event.event_type, event.raw_location, event.raw_review,
        event.location_id or "-", event.review_resource_name or "-", event.review_id or "-",
        stage, f" review_issue={event.review_issue!r}" if event.review_issue else "",
    )

    if event.event_type != "NEW_REVIEW":
        logger.info(
            "WEBHOOK_RESULT message_id=%s event=%s final_status=IGNORED http_status=200",
            event.message_id or "-", event.event_type,
        )
        return JSONResponse({"status": AutomationStatus.IGNORED.value})

    run = await process_review_notification(event)
    status_code = _http_status(run)
    logger.log(
        logging.WARNING if status_code >= 500 else logging.INFO,
        "WEBHOOK_RESULT message_id=%s review_id=%s run_id=%s resolution=%s final_status=%s "
        "http_status=%d retryable=%s error_stage=%s error_type=%s",
        event.message_id or "-", run.review_id or "-", run.run_id, run.resolution,
        run.status.value, status_code, run.retryable, run.error_stage or "-",
        (run.error or "").split(":", 1)[0] or "-",
    )
    content = {
        "status": run.status.value,
        "run_id": run.run_id,
        "review_id": run.review_id or None,
        "resolution": run.resolution,
    }
    return JSONResponse(content, status_code=status_code)


def _http_status(run: AutomationRun) -> int:
    if not run.retryable:
        return 200
    if run.status is AutomationStatus.DUPLICATE:
        return 409
    return 503
