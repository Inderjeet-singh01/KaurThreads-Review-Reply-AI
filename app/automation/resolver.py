"""Resolve a NEW_REVIEW notification to exactly one review, then process it.

The webhook used to reject (HTTP 400) every NEW_REVIEW whose review
reference did not match one exact resource-name pattern — even when the
location was valid and Google could tell us which review was new. This
module replaces that dead end with deterministic rules::

    A. review reference parsed  ──> process_new_review(that review)
         (Google 404 ──> SKIPPED_NOT_FOUND, acknowledged; NOT a fallback:
          a well-formed id Google does not know — e.g. a synthetic test
          push — must never cause a reply to some other review)
    B. location valid, review reference missing / unparseable
         ──> FALLBACK_RESOLUTION
             list the location's reviews from Google (source of truth)
             keep unanswered reviews created/updated within
               RECONCILIATION_LOOKBACK_MINUTES, newest first
             skip reviews another run is processing / recently settled
             ├─ none left ──> SKIPPED_NO_UNANSWERED_REVIEW (acknowledged)
             └─ newest one ──> process_new_review(it)     ← same processor

Only ONE review is processed per notification; older unanswered reviews
are left to the next notification or the scheduled reconciliation
(:mod:`app.automation.reconcile`), which share :func:`list_recent_unanswered`.

Duplicate deliveries of a fallback message are recognized by Pub/Sub
message id (in-process); whatever happens, every publish is still guarded
by the processor's two Google "already replied" checks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.auth.google_oauth import GoogleOAuthError
from app.automation.processor import (
    MAX_PROCESSING_ATTEMPTS,
    AutomationRun,
    AutomationStatus,
    _is_retryable_google_error,
    _location_allowed,
    _set_status,
    _stop_with_error,
    process_new_review,
    review_activity,
)
from app.config import settings
from app.google.client import GoogleAPIError, get_google_client
from app.google.reviews import (
    GoogleReviewError,
    get_location_name,
    get_unanswered_reviews,
    has_reply,
)
from app.webhooks.pubsub import ReviewNotification

logger = logging.getLogger(__name__)

_MESSAGE_MEMORY_TTL_SECONDS = 24 * 60 * 60
_MAX_REMEMBERED_MESSAGES = 1000
_FRACTION_RE = re.compile(r"(\.\d{6})\d+")
_MIN_TIME = datetime.min.replace(tzinfo=timezone.utc)


# --- Candidate listing (shared with reconciliation) ---------------------------------
@dataclass(frozen=True)
class Candidate:
    """An unanswered review, as Google listed it."""

    review_id: str
    created_at: datetime | None
    updated_at: datetime | None

    @property
    def latest_activity(self) -> datetime | None:
        times = [t for t in (self.created_at, self.updated_at) if t is not None]
        return max(times) if times else None

    def summary(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def parse_google_timestamp(value: Any) -> datetime | None:
    """RFC 3339 timestamp from the Reviews API -> aware datetime (else None)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = _FRACTION_RE.sub(r"\1", value.strip())  # nanoseconds -> microseconds
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def select_recent_unanswered(
    raw_reviews: list[dict[str, Any]],
    lookback_minutes: int | None = None,
    now: datetime | None = None,
) -> list[Candidate]:
    """Unanswered reviews within the lookback window, newest first.

    Deterministic order: creation time, then update time, then review id
    (all descending). With a lookback window, reviews without a usable
    timestamp are excluded — never guess that an undated review is new.
    """
    lookback = settings.reconciliation_lookback_minutes if lookback_minutes is None else lookback_minutes
    cutoff = None
    if lookback > 0:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=lookback)

    seen: set[str] = set()
    candidates: list[Candidate] = []
    for raw in raw_reviews:
        review_id = raw.get("reviewId")
        if not isinstance(review_id, str) or not review_id or review_id in seen:
            continue
        if has_reply(raw):
            continue
        candidate = Candidate(
            review_id=review_id,
            created_at=parse_google_timestamp(raw.get("createTime")),
            updated_at=parse_google_timestamp(raw.get("updateTime")),
        )
        if cutoff is not None:
            activity = candidate.latest_activity
            if activity is None or activity < cutoff:
                continue
        seen.add(review_id)
        candidates.append(candidate)

    candidates.sort(
        key=lambda c: (
            c.created_at or c.updated_at or _MIN_TIME,
            c.updated_at or c.created_at or _MIN_TIME,
            c.review_id,
        ),
        reverse=True,
    )
    return candidates


def list_recent_unanswered(
    location_id: str | None,
    lookback_minutes: int | None = None,
) -> tuple[str, list[Candidate]]:
    """Fetch the location's reviews from Google and select recent unanswered ones.

    Blocking (run it in a worker thread). Returns the bare location id and
    the candidates, newest first. Uses the same location resolution and
    "unanswered" rule as GET /reviews and the bulk backfill.
    """
    client = get_google_client()
    location_name = get_location_name(client, location_id)
    raw_reviews = get_unanswered_reviews(client, location_name)
    candidates = select_recent_unanswered(raw_reviews, lookback_minutes)
    logger.info(
        "Recent unanswered reviews for %s: %d of %d unanswered within %s minutes",
        location_name, len(candidates), len(raw_reviews),
        settings.reconciliation_lookback_minutes if lookback_minutes is None else lookback_minutes,
    )
    return location_name.rsplit("/", 1)[-1], candidates


# --- Message-level duplicate memory (fallback path only) ------------------------------
_inflight_messages: set[str] = set()
_settled_messages: dict[str, tuple[str, float]] = {}
_message_failures: dict[str, int] = {}


def reset_state() -> None:
    """Forget the message memory (tests only)."""
    _inflight_messages.clear()
    _settled_messages.clear()
    _message_failures.clear()


def _settled_status(message_id: str) -> str | None:
    entry = _settled_messages.get(message_id)
    if entry is None:
        return None
    status, recorded_at = entry
    if time.monotonic() - recorded_at > _MESSAGE_MEMORY_TTL_SECONDS:
        _settled_messages.pop(message_id, None)
        return None
    return status


def _settle_message(message_id: str, status: AutomationStatus) -> None:
    now = time.monotonic()
    for key, (_, recorded_at) in list(_settled_messages.items()):
        if now - recorded_at > _MESSAGE_MEMORY_TTL_SECONDS:
            del _settled_messages[key]
    while len(_settled_messages) >= _MAX_REMEMBERED_MESSAGES:
        del _settled_messages[next(iter(_settled_messages))]
    _settled_messages[message_id] = (status.value, now)
    _message_failures.pop(message_id, None)


def _bound_message_retries(run: AutomationRun, message_id: str | None, delivery_attempt: int | None) -> None:
    """Cap redeliveries of a fallback message whose Google listing keeps failing."""
    if not run.retryable:
        return
    failures = 1
    if message_id:
        failures = _message_failures.get(message_id, 0) + 1
        _message_failures[message_id] = failures
    attempts = max(failures, delivery_attempt or 0)
    if attempts >= MAX_PROCESSING_ATTEMPTS:
        run.retryable = False
        run.error = (
            f"{run.error} | gave up after {attempts} attempts; the scheduled "
            "reconciliation will pick the review up"
        )
        logger.warning(
            "automation run=%s message=%s fallback retry budget exhausted (%d attempts); "
            "acknowledging — reconciliation is the safety net",
            run.run_id, message_id, attempts,
        )


def _log_summary(run: AutomationRun) -> AutomationRun:
    logger.info("automation_run %s", json.dumps(run.summary(), default=str))
    return run


# --- Entry point ----------------------------------------------------------------------
def _location_issue(location_id: str | None) -> str | None:
    """Why a notification's location can never be processed (else ``None``).

    Checked before any Google call. Business Profile location ids are
    numeric; anything else (``TEST_LOCATION``, ``YOUR_LOCATION_ID``) is a
    synthetic or placeholder push. When GOOGLE_LOCATION_ID is configured it
    stays authoritative: notifications for another location are not ours.
    """
    if not location_id or not location_id.isdigit():
        return "location id is not a Business Profile location id"
    configured = settings.google_location_id.strip().strip("/")
    if configured and configured.rsplit("/", 1)[-1] != location_id:
        return "location does not match GOOGLE_LOCATION_ID"
    return None


async def process_review_notification(event: ReviewNotification) -> AutomationRun:
    """Process a decoded NEW_REVIEW notification (exact review, else fallback)."""
    if (issue := _location_issue(event.location_id)) is not None:
        # Permanent: acknowledge (2xx) so Pub/Sub stops redelivering it.
        run = AutomationRun(
            review_id=event.review_id or "",
            location_id=event.location_id,
            event_type=event.event_type,
            review_resource_name=event.review_resource_name,
            trigger="webhook",
            message_id=event.message_id,
        )
        run.error_stage = "location_check"
        run.error = f"InvalidNotificationLocation: {issue}"
        _set_status(
            run, AutomationStatus.IGNORED,
            f"reason={issue!r} location_raw={event.raw_location} review_raw={event.raw_review}",
        )
        return _log_summary(run)
    if event.review_id:
        return await process_new_review(
            event.review_id,
            event.location_id,
            event.review_resource_name,
            event_type=event.event_type,
            delivery_attempt=event.delivery_attempt,
            trigger="webhook",
            message_id=event.message_id,
        )
    return await _resolve_by_fallback(event, event.review_issue or "review reference unusable")


async def _resolve_by_fallback(event: ReviewNotification, reason: str) -> AutomationRun:
    run = AutomationRun(
        review_id="",
        location_id=event.location_id,
        event_type=event.event_type,
        trigger="webhook",
        message_id=event.message_id,
        resolution="fallback",
        fallback_reason=reason,
    )
    message_id = event.message_id

    # Same gates, in the same order, as process_new_review — before any call.
    if not settings.auto_reply_enabled:
        _set_status(run, AutomationStatus.DISABLED, "reason=AUTO_REPLY_ENABLED=false")
        return _log_summary(run)
    if not _location_allowed(event.location_id):
        _set_status(run, AutomationStatus.IGNORED, "reason=location not in AUTO_REPLY_LOCATION_IDS")
        return _log_summary(run)
    if message_id and message_id in _inflight_messages:
        run.retryable = True
        _set_status(run, AutomationStatus.DUPLICATE, "reason=message already being resolved")
        return _log_summary(run)
    if message_id and (previous := _settled_status(message_id)) is not None:
        _set_status(run, AutomationStatus.DUPLICATE, f"reason=message already resolved as {previous}")
        return _log_summary(run)

    _set_status(
        run, AutomationStatus.FALLBACK_RESOLUTION,
        f"reason={reason!r} review_raw={event.raw_review} location_raw={event.raw_location}",
    )
    if message_id:
        _inflight_messages.add(message_id)
    try:
        try:
            location_id, candidates = await asyncio.to_thread(
                list_recent_unanswered, event.location_id
            )
        except (GoogleReviewError, GoogleOAuthError, GoogleAPIError) as exc:
            _stop_with_error(
                run, "fallback_listing", exc, retryable=_is_retryable_google_error(exc)
            )
            _bound_message_retries(run, message_id, event.delivery_attempt)
            return _log_summary(run)

        run.location_id = location_id
        activity = {c.review_id: review_activity(c.review_id) for c in candidates}
        free = [c for c in candidates if activity[c.review_id] is None]
        in_progress = [c.review_id for c in candidates if activity[c.review_id] == "in_progress"]
        logger.info(
            "automation run=%s message=%s FALLBACK_RESOLUTION candidates=%s in_progress=%s "
            "recently_settled=%d",
            run.run_id, message_id, [c.review_id for c in candidates[:5]], in_progress,
            sum(1 for state in activity.values() if state == "recently_settled"),
        )
        if not free:
            if in_progress:
                # Most likely this very review, picked by another delivery or
                # the reconciliation: let Pub/Sub retry once that run is done.
                run.retryable = True
                _set_status(run, AutomationStatus.DUPLICATE, "reason=candidate already processing")
            else:
                _set_status(
                    run, AutomationStatus.SKIPPED_NO_UNANSWERED_REVIEW,
                    f"candidates={len(candidates)}",
                )
            return _log_summary(run)

        chosen = free[0]
        logger.info(
            "automation message=%s FALLBACK_RESOLUTION chose review=%s created=%s "
            "(newest of %d free candidates)",
            message_id, chosen.review_id,
            chosen.created_at.isoformat() if chosen.created_at else None, len(free),
        )
        run = await process_new_review(
            chosen.review_id,
            location_id,
            None,
            event_type=event.event_type,
            delivery_attempt=event.delivery_attempt,
            trigger="webhook",
            message_id=message_id,
            resolution="fallback",
            fallback_reason=reason,
        )
        return run
    finally:
        if message_id:
            _inflight_messages.discard(message_id)
            if not run.retryable:
                _settle_message(message_id, run.status)
