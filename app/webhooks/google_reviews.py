"""POST /webhooks/google-reviews — Business Profile new-review notifications.

Google Business Profile publishes a NEW_REVIEW notification to a Cloud
Pub/Sub topic; an authenticated push subscription delivers it here. This
handler stays thin: authenticate, decode, hand the review to
:func:`app.automation.processor.process_new_review`, and translate the
outcome into Pub/Sub acknowledgement semantics:

* 2xx  — acknowledged: processed, including "validation rejected the
  reply", "already replied", "automation disabled" and ignored events.
  These must not be redelivered.
* 409  — the same review is being processed right now; redeliver later.
* 503  — transient Google / AI infrastructure failure; redeliver (bounded
  by the processor's retry budget and the subscription's retry policy).
* 400  — undecodable message (redelivery cannot fix it; configure a
  dead-letter topic to collect these for inspection).
* 401 / 403 — not an authenticated Pub/Sub push; nothing is processed.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.automation.processor import AutomationStatus, process_new_review
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
        logger.warning("Pub/Sub push rejected: body is not JSON")
        raise HTTPException(status_code=400, detail="Body must be a Pub/Sub push JSON envelope.")
    try:
        event = parse_push_body(body)
    except MalformedPushError as exc:
        logger.warning("Pub/Sub push rejected: %s", exc)
        raise HTTPException(status_code=400, detail="Malformed Pub/Sub message.")

    if event.event_type != "NEW_REVIEW" or not event.review_id:
        logger.info(
            "Pub/Sub notification ignored: type=%s message=%s",
            event.event_type, event.message_id,
        )
        return JSONResponse({"status": AutomationStatus.IGNORED.value})

    run = await process_new_review(
        event.review_id,
        event.location_id,
        event.review_resource_name,
        event_type=event.event_type,
        delivery_attempt=event.delivery_attempt,
    )
    content = {"status": run.status.value, "run_id": run.run_id}
    if not run.retryable:
        return JSONResponse(content)
    if run.status is AutomationStatus.DUPLICATE:
        return JSONResponse(content, status_code=409)
    return JSONResponse(content, status_code=503)
