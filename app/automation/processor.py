"""Automatic processing of one new Google review.

This module orchestrates the automatic workflow. It reuses the exact same
building blocks as the manual flow; nothing here talks to an AI SDK or the
Google REST API directly:

* :func:`app.google.reviews.get_review` / :func:`~app.google.reviews.has_reply`
  — Google is the source of truth for the review and its reply state,
* :func:`app.ai.reply_generator.generate_review_reply_with_provider`
  — Groq primary, Gemini fallback, shared prompt,
* :func:`app.ai.reply_validator.validate_review_reply`
  — the same validator as the manual "Check Reply" button
  (deterministic checks first, then the AI suitability check),
* :func:`app.google.reviews.publish_reply` — the same publish call as the
  manual "Post Reply" button.

Flow (hard upper bounds, no loops)::

    AUTO_REPLY_ENABLED? ── no ──> DISABLED          (no Google / AI calls)
      │
    fetch review ── already replied ──> SKIPPED_ALREADY_REPLIED (no AI calls)
      │
    generate (Groq → Gemini) ── both fail ──> FAILED_GENERATION
      │
    validate ── PASS ─────────────────────────────┐
      │ FAIL                                      │
    regenerate ONCE with the failure reason       │
      │                                           │
    validate again ── FAIL ──> FAILED_VALIDATION  │
      │ PASS                                      │
      ├───────────────────────────────────────────┘
    final Google check ── replied meanwhile ──> SKIPPED_ALREADY_REPLIED
      │
    AUTO_REPLY_DRY_RUN? ── yes ──> DRY_RUN (WOULD_PUBLISH, nothing sent)
      │
    publish_reply ──> PUBLISHED

A validator *infrastructure* error (timeout, quota, bad provider response)
is not a FAIL verdict: it stops the run as a retryable ERROR without
regenerating.

Duplicate protection (in-process): a per-review lock rejects a second run
for a review that is already being processed, recent terminal outcomes
(validation rejected, dry run, retry budget exhausted) are remembered so a
redelivered event does not spend AI calls again, and retryable failures are
capped per review. None of this replaces the final Google check, which runs
immediately before every publish. This state lives in one process: run the
backend as a single worker / single instance (see README).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.ai.reply_generator import (
    ReplyGenerationError,
    generate_review_reply_with_provider,
)
from app.ai.reply_validator import ReplyValidationError, validate_review_reply
from app.auth.google_oauth import GoogleOAuthError
from app.config import settings
from app.google.client import GoogleAPIError
from app.google.reviews import (
    GoogleReviewError,
    ReviewNotFoundError,
    get_review,
    has_reply,
    normalize_review,
    publish_reply,
)

logger = logging.getLogger(__name__)

NEW_REVIEW_EVENT = "NEW_REVIEW"

# Retryable failures (Google/AI outages) allowed per review before the run is
# acknowledged anyway and left for manual review. Bounds AI spend even when
# no Pub/Sub dead-letter policy is configured.
MAX_PROCESSING_ATTEMPTS = 3
# How long a terminal outcome is remembered for duplicate deliveries.
_RECENT_OUTCOME_TTL_SECONDS = 24 * 60 * 60
# Logged reply text is truncated (it is public content, never a secret).
_MAX_LOGGED_REPLY_CHARS = 600


class AutomationStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    SKIPPED_ALREADY_REPLIED = "SKIPPED_ALREADY_REPLIED"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"
    VALIDATING = "VALIDATING"
    PASSED = "PASSED"
    REGENERATING = "REGENERATING"
    REGENERATED = "REGENERATED"
    FAILED_VALIDATION = "FAILED_VALIDATION"
    FAILED_GENERATION = "FAILED_GENERATION"
    FINAL_CHECK = "FINAL_CHECK"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    DRY_RUN = "DRY_RUN"
    ERROR = "ERROR"
    # Runs that stop before any work:
    DISABLED = "DISABLED"  # AUTO_REPLY_ENABLED=false
    IGNORED = "IGNORED"  # not a NEW_REVIEW event, or location not allowlisted
    DUPLICATE = "DUPLICATE"  # same review in progress / recently finished
    SKIPPED_NOT_FOUND = "SKIPPED_NOT_FOUND"  # review deleted on Google


@dataclass
class AutomationRun:
    """Record of one automation run (logged as one structured summary line)."""

    review_id: str
    location_id: str | None
    event_type: str
    review_resource_name: str | None = None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: AutomationStatus = AutomationStatus.RECEIVED
    # True when the failure is transient and the Pub/Sub message should be
    # redelivered (the webhook answers non-2xx).
    retryable: bool = False
    generation_provider: str | None = None
    regeneration_provider: str | None = None
    regeneration_attempted: bool = False
    validation_results: list[str] = field(default_factory=list)
    validation_reasons: list[str | None] = field(default_factory=list)
    publish_result: str = "not_attempted"
    reply: str | None = None
    error_stage: str | None = None
    error: str | None = None

    @property
    def published(self) -> bool:
        return self.status is AutomationStatus.PUBLISHED

    def summary(self) -> dict[str, Any]:
        reply = self.reply
        if reply and len(reply) > _MAX_LOGGED_REPLY_CHARS:
            reply = reply[:_MAX_LOGGED_REPLY_CHARS] + "…"
        return {
            "run_id": self.run_id,
            "review_id": self.review_id,
            "location_id": self.location_id,
            "event_type": self.event_type,
            "final_status": self.status.value,
            "retryable": self.retryable,
            "generation_provider": self.generation_provider,
            "regeneration_attempted": self.regeneration_attempted,
            "regeneration_provider": self.regeneration_provider,
            "validation_results": self.validation_results,
            "validation_reasons": self.validation_reasons,
            "publish_result": self.publish_result,
            "published": self.published,
            "reply": reply,
            "error_stage": self.error_stage,
            "error": self.error,
        }


# --- In-process duplicate protection -------------------------------------------
_locks: dict[str, asyncio.Lock] = {}
_recent_outcomes: dict[str, tuple[AutomationStatus, float]] = {}
_retryable_failures: dict[str, int] = {}

# Outcomes after which a redelivered event must not spend AI calls again.
# PUBLISHED / SKIPPED_ALREADY_REPLIED are not listed: the Google check on the
# next run detects the existing reply without any AI call.
_REMEMBERED_OUTCOMES = {
    AutomationStatus.FAILED_VALIDATION,
    AutomationStatus.DRY_RUN,
}


def reset_state() -> None:
    """Forget all in-process automation state (tests only)."""
    _locks.clear()
    _recent_outcomes.clear()
    _retryable_failures.clear()


def _recent_outcome(review_id: str) -> AutomationStatus | None:
    entry = _recent_outcomes.get(review_id)
    if entry is None:
        return None
    status, recorded_at = entry
    if time.monotonic() - recorded_at > _RECENT_OUTCOME_TTL_SECONDS:
        _recent_outcomes.pop(review_id, None)
        return None
    return status


def _remember_outcome(review_id: str, status: AutomationStatus) -> None:
    now = time.monotonic()
    for key, (_, recorded_at) in list(_recent_outcomes.items()):
        if now - recorded_at > _RECENT_OUTCOME_TTL_SECONDS:
            del _recent_outcomes[key]
    _recent_outcomes[review_id] = (status, now)


def _set_status(run: AutomationRun, status: AutomationStatus, detail: str = "") -> None:
    run.status = status
    logger.info(
        "automation run=%s review=%s location=%s status=%s%s",
        run.run_id, run.review_id, run.location_id, status.value,
        f" {detail}" if detail else "",
    )


def _stop_with_error(
    run: AutomationRun, stage: str, exc: Exception, *, retryable: bool,
    status: AutomationStatus = AutomationStatus.ERROR,
) -> AutomationRun:
    # Exception messages in this app are secret-free by construction
    # (Google/AI errors carry status + provider text, never tokens or keys).
    run.error_stage = stage
    run.error = f"{type(exc).__name__}: {exc}"[:500]
    run.retryable = retryable
    _set_status(run, status, f"stage={stage} retryable={retryable}")
    return run


def _is_retryable_google_error(exc: Exception) -> bool:
    """Transient Google failures worth a Pub/Sub redelivery."""
    if isinstance(exc, GoogleOAuthError):
        return True  # e.g. token refresh hiccup; bounded by the retry budget
    if isinstance(exc, GoogleAPIError):
        return exc.status is None or exc.status in (401, 408, 429) or exc.status >= 500
    # GoogleReviewError subclasses: LocationNotFoundError wraps API failures
    # while resolving the location, so give it the bounded retry too.
    return not isinstance(exc, ReviewNotFoundError)


def _location_allowed(location_id: str | None) -> bool:
    allowlist = settings.auto_reply_location_list
    if not allowlist:
        return True
    effective = (location_id or settings.google_location_id or "").strip().rstrip("/")
    return bool(effective) and effective.rsplit("/", 1)[-1] in allowlist


# --- Pipeline (synchronous: runs in a worker thread) ------------------------------
def _fetch_review(run: AutomationRun, stage: str) -> dict[str, Any] | None:
    """Fetch the authoritative review from Google; ``None`` means stop."""
    try:
        return get_review(run.review_id, location_id=run.location_id)
    except ReviewNotFoundError as exc:
        _stop_with_error(
            run, stage, exc, retryable=False, status=AutomationStatus.SKIPPED_NOT_FOUND
        )
    except (GoogleReviewError, GoogleOAuthError, GoogleAPIError) as exc:
        _stop_with_error(run, stage, exc, retryable=_is_retryable_google_error(exc))
    return None


def _run_pipeline(run: AutomationRun) -> AutomationRun:
    _set_status(run, AutomationStatus.PROCESSING)

    # 1. Authoritative review from Google + first "already replied" check.
    raw_review = _fetch_review(run, "fetch")
    if raw_review is None:
        return run
    if has_reply(raw_review):
        _set_status(run, AutomationStatus.SKIPPED_ALREADY_REPLIED, "stage=initial_check")
        return run
    review = normalize_review(raw_review)

    # 2. Initial generation (Groq → Gemini fallback inside the generator).
    _set_status(run, AutomationStatus.GENERATING)
    try:
        reply, run.generation_provider = generate_review_reply_with_provider(review)
    except ReplyGenerationError as exc:
        return _stop_with_error(
            run, "generation", exc, retryable=True,
            status=AutomationStatus.FAILED_GENERATION,
        )
    run.reply = reply
    _set_status(run, AutomationStatus.GENERATED, f"provider={run.generation_provider}")

    # 3. Validation with the same function as the manual Check Reply.
    _set_status(run, AutomationStatus.VALIDATING, "attempt=1")
    try:
        verdict = validate_review_reply(review, reply)
    except ReplyValidationError as exc:
        # The validator could not run: an infrastructure error, NOT a FAIL.
        # Do not regenerate; let Pub/Sub retry later (bounded).
        return _stop_with_error(run, "validation", exc, retryable=True)
    run.validation_results.append(verdict.decision)
    run.validation_reasons.append(verdict.reason)

    # 4. At most ONE regeneration, guided by the validator's reason.
    if not verdict.passed:
        if settings.auto_reply_max_regenerations < 1:
            run.error_stage = "validation"
            _set_status(run, AutomationStatus.FAILED_VALIDATION, "regeneration disabled")
            return run
        run.regeneration_attempted = True
        _set_status(run, AutomationStatus.REGENERATING, f"reason={verdict.reason!r}")
        try:
            reply, run.regeneration_provider = generate_review_reply_with_provider(
                review, revision_feedback=verdict.reason
            )
        except ReplyGenerationError as exc:
            return _stop_with_error(
                run, "regeneration", exc, retryable=True,
                status=AutomationStatus.FAILED_GENERATION,
            )
        run.reply = reply
        _set_status(run, AutomationStatus.REGENERATED, f"provider={run.regeneration_provider}")

        _set_status(run, AutomationStatus.VALIDATING, "attempt=2")
        try:
            verdict = validate_review_reply(review, reply)
        except ReplyValidationError as exc:
            return _stop_with_error(run, "revalidation", exc, retryable=True)
        run.validation_results.append(verdict.decision)
        run.validation_reasons.append(verdict.reason)
        if not verdict.passed:
            # Final: never regenerate a second time. Left for manual review.
            run.error_stage = "revalidation"
            _set_status(
                run, AutomationStatus.FAILED_VALIDATION,
                f"reason={verdict.reason!r} -> manual review",
            )
            return run

    approved_reply = reply
    _set_status(run, AutomationStatus.PASSED)

    # 5. Final Google check immediately before publishing: someone may have
    #    replied on Google while the AI was working.
    _set_status(run, AutomationStatus.FINAL_CHECK)
    raw_review = _fetch_review(run, "final_check")
    if raw_review is None:
        return run
    if has_reply(raw_review):
        run.publish_result = "blocked_existing_reply"
        _set_status(run, AutomationStatus.SKIPPED_ALREADY_REPLIED, "stage=final_check")
        return run

    # 6. Publishing gate. Re-read the switches at the last possible moment.
    if not settings.auto_reply_enabled:
        _set_status(run, AutomationStatus.DISABLED, "stage=publish_gate")
        return run
    if settings.auto_reply_dry_run:
        run.publish_result = "dry_run"
        logger.info(
            "automation run=%s review=%s WOULD_PUBLISH reply=%s",
            run.run_id, run.review_id, json.dumps(approved_reply),
        )
        _set_status(run, AutomationStatus.DRY_RUN)
        return run

    _set_status(run, AutomationStatus.PUBLISHING)
    try:
        publish_reply(run.review_id, approved_reply, location_id=run.location_id)
    except (GoogleReviewError, GoogleOAuthError, GoogleAPIError) as exc:
        run.publish_result = "failed"
        # On a retry, the final check detects a reply that Google stored
        # despite a failed response, so redelivery cannot double-publish.
        return _stop_with_error(run, "publish", exc, retryable=_is_retryable_google_error(exc))
    run.publish_result = "published"
    _set_status(run, AutomationStatus.PUBLISHED)
    return run


# --- Entry point --------------------------------------------------------------------
async def process_new_review(
    review_id: str,
    location_id: str | None,
    review_resource_name: str | None = None,
    *,
    event_type: str = NEW_REVIEW_EVENT,
    delivery_attempt: int | None = None,
    allow_rerun: bool = False,
) -> AutomationRun:
    """Run the automatic reply workflow for one review.

    Used by the Pub/Sub webhook, the bulk backfill
    (:mod:`app.automation.backfill`) and the development test endpoint alike.
    ``delivery_attempt`` is Pub/Sub's counter (present when a dead-letter
    policy is configured). ``allow_rerun`` skips only the "recently
    finished" duplicate memory (development test endpoint); every safety
    gate and Google check still applies.

    Never raises for expected failures: the returned run's ``status`` and
    ``retryable`` describe the outcome.
    """
    run = AutomationRun(
        review_id=review_id,
        location_id=location_id,
        event_type=event_type,
        review_resource_name=review_resource_name,
    )
    _set_status(
        run, AutomationStatus.RECEIVED,
        f"event={event_type} resource={review_resource_name} delivery_attempt={delivery_attempt}",
    )

    if event_type != NEW_REVIEW_EVENT:
        _set_status(run, AutomationStatus.IGNORED, "reason=not a NEW_REVIEW event")
    elif not settings.auto_reply_enabled:
        _set_status(run, AutomationStatus.DISABLED, "reason=AUTO_REPLY_ENABLED=false")
    elif not _location_allowed(location_id):
        _set_status(run, AutomationStatus.IGNORED, "reason=location not in AUTO_REPLY_LOCATION_IDS")
    elif not allow_rerun and (previous := _recent_outcome(review_id)) is not None:
        _set_status(run, AutomationStatus.DUPLICATE, f"reason=recently finished as {previous.value}")
    else:
        lock = _locks.setdefault(review_id, asyncio.Lock())
        if lock.locked():
            # Retryable so Pub/Sub redelivers later; by then the first run has
            # finished and the Google check / outcome memory stops it cheaply.
            run.retryable = True
            _set_status(run, AutomationStatus.DUPLICATE, "reason=already processing")
        else:
            async with lock:
                try:
                    await asyncio.to_thread(_run_pipeline, run)
                except Exception as exc:  # defensive: never crash the webhook
                    logger.exception("automation run=%s crashed", run.run_id)
                    _stop_with_error(run, "unexpected", exc, retryable=True)
            if not lock.locked():
                _locks.pop(review_id, None)
            _record_outcome(run, delivery_attempt)

    logger.info("automation_run %s", json.dumps(run.summary(), default=str))
    return run


def _record_outcome(run: AutomationRun, delivery_attempt: int | None) -> None:
    """Update the retry budget and the duplicate-outcome memory."""
    if not run.retryable:
        _retryable_failures.pop(run.review_id, None)
        if run.status in _REMEMBERED_OUTCOMES:
            _remember_outcome(run.review_id, run.status)
        return

    failures = _retryable_failures.get(run.review_id, 0) + 1
    _retryable_failures[run.review_id] = failures
    attempts = max(failures, delivery_attempt or 0)
    if attempts >= MAX_PROCESSING_ATTEMPTS:
        # Stop the redelivery cycle: acknowledge and leave it for a human.
        run.retryable = False
        run.error = f"{run.error} | gave up after {attempts} attempts; manual review needed"
        _retryable_failures.pop(run.review_id, None)
        _remember_outcome(run.review_id, run.status)
        logger.warning(
            "automation run=%s review=%s retry budget exhausted (%d attempts); "
            "acknowledging without publishing — reply manually",
            run.run_id, run.review_id, attempts,
        )
