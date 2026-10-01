"""Backfill: reply to every currently unanswered review of one location.

A backfill is a thin sequential loop around
:func:`app.automation.processor.process_new_review` — the exact pipeline the
Pub/Sub webhook uses. Nothing here generates, validates or publishes on its
own, so every safety rule of the real-time path applies per review:
AUTO_REPLY_ENABLED / AUTO_REPLY_DRY_RUN, the fresh Google fetch and
"already replied" check, at most one regeneration, the final Google check
immediately before publishing, the per-review lock and the retry budget.

Flow::

    start_backfill(location)
      ├─ another backfill running? ──> BackfillRejected (409)
      ├─ list unanswered reviews (same helper as GET /reviews)
      └─ background task, ONE review at a time:
           process_new_review(review)          ← same as the webhook
             retryable failure? retry, at most MAX_PROCESSING_ATTEMPTS
           record outcome; a failure never stops the batch
           pause AUTOMATION_BACKFILL_DELAY_SECONDS

Job state lives in this process only (like the processor's duplicate
memory): a restart loses the job record, but never causes a double reply —
already-answered reviews drop out of the unanswered list, and every publish
is preceded by a Google check. Starting again simply continues with the
reviews that are still unanswered.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.automation.processor import (
    MAX_PROCESSING_ATTEMPTS,
    AutomationRun,
    AutomationStatus,
    _location_allowed,
    forget_review,
    process_new_review,
)
from app.config import settings
from app.google.client import get_google_client
from app.google.reviews import get_location_name, get_unanswered_reviews

logger = logging.getLogger(__name__)

# Finished jobs kept for the status endpoint (oldest dropped first).
_MAX_FINISHED_JOBS = 20


class BackfillJobStatus(str, Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"  # the job itself crashed; per-review failures do not count
    CANCELLED = "CANCELLED"


class BackfillOutcome(str, Enum):
    """Per-review result category shown in the UI."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PUBLISHED = "PUBLISHED"
    DRY_RUN = "DRY_RUN"  # WOULD_PUBLISH: passed every check, nothing sent
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"  # never started because the job was cancelled


_SKIPPED_STATUSES = {
    AutomationStatus.SKIPPED_ALREADY_REPLIED,
    AutomationStatus.SKIPPED_NOT_FOUND,
    AutomationStatus.DUPLICATE,
    AutomationStatus.IGNORED,
    AutomationStatus.DISABLED,
}


class BackfillRejected(Exception):
    """A backfill could not be started (already running, disabled, ...)."""

    def __init__(self, reason: str, status_code: int = 409, job_id: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code
        self.job_id = job_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class BackfillItem:
    review_id: str
    outcome: BackfillOutcome = BackfillOutcome.PENDING
    final_status: str | None = None  # the processor's AutomationStatus
    run_id: str | None = None
    attempts: int = 0
    generation_provider: str | None = None
    validation_results: list[str] = field(default_factory=list)
    publish_result: str | None = None
    error_stage: str | None = None
    error: str | None = None
    retryable: bool = False
    started_at: str | None = None
    finished_at: str | None = None

    def record(self, run: AutomationRun) -> None:
        self.final_status = run.status.value
        self.run_id = run.run_id
        self.generation_provider = run.regeneration_provider or run.generation_provider
        self.validation_results = list(run.validation_results)
        self.publish_result = run.publish_result
        self.error_stage = run.error_stage
        self.error = run.error
        self.retryable = run.retryable
        if run.status is AutomationStatus.PUBLISHED:
            self.outcome = BackfillOutcome.PUBLISHED
        elif run.status is AutomationStatus.DRY_RUN:
            self.outcome = BackfillOutcome.DRY_RUN
        elif run.status in _SKIPPED_STATUSES and not run.retryable:
            self.outcome = BackfillOutcome.SKIPPED
            if run.status is AutomationStatus.DUPLICATE and not self.error:
                self.error = "Recently processed by the automation; not retried."
        else:
            self.outcome = BackfillOutcome.FAILED
            if not self.error:
                if run.status is AutomationStatus.FAILED_VALIDATION:
                    self.error = "The reply did not pass validation; reply manually."
                elif run.status is AutomationStatus.DUPLICATE:
                    self.error = "Another automation run was processing this review."
                else:
                    self.error = f"Stopped as {run.status.value}."

    def summary(self) -> dict[str, Any]:
        # Never includes reply text, tokens or credentials.
        return {
            "review_id": self.review_id,
            "outcome": self.outcome.value,
            "final_status": self.final_status,
            "run_id": self.run_id,
            "attempts": self.attempts,
            "generation_provider": self.generation_provider,
            "validation_results": self.validation_results,
            "publish_result": self.publish_result,
            "error_stage": self.error_stage,
            "error": self.error,
            "retryable": self.retryable,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class BackfillJob:
    location_id: str | None
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: BackfillJobStatus = BackfillJobStatus.STARTING
    dry_run: bool = False
    items: list[BackfillItem] = field(default_factory=list)
    current_review_id: str | None = None
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    error: str | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def active(self) -> bool:
        return self.status in (BackfillJobStatus.STARTING, BackfillJobStatus.RUNNING)

    def _count(self, *outcomes: BackfillOutcome) -> int:
        return sum(1 for item in self.items if item.outcome in outcomes)

    def summary(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "location_id": self.location_id,
            "dry_run": self.dry_run,
            "total": len(self.items),
            "processed": self._count(
                BackfillOutcome.PUBLISHED, BackfillOutcome.DRY_RUN,
                BackfillOutcome.SKIPPED, BackfillOutcome.FAILED,
            ),
            "published": self._count(BackfillOutcome.PUBLISHED),
            "would_publish": self._count(BackfillOutcome.DRY_RUN),
            "skipped": self._count(BackfillOutcome.SKIPPED),
            "failed": self._count(BackfillOutcome.FAILED),
            "cancelled": self._count(BackfillOutcome.CANCELLED),
            "current_review_id": self.current_review_id,
            "cancel_requested": self.cancel_event.is_set(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "items": [item.summary() for item in self.items],
        }


# --- In-process job registry -------------------------------------------------------
_jobs: dict[str, BackfillJob] = {}
_active_job_id: str | None = None
# Strong references so running tasks are not garbage-collected.
_tasks: set[asyncio.Task] = set()


def reset_state() -> None:
    """Forget all backfill jobs (tests only)."""
    global _active_job_id
    for task in _tasks:
        task.cancel()
    _tasks.clear()
    _jobs.clear()
    _active_job_id = None


def get_job(job_id: str) -> BackfillJob | None:
    return _jobs.get(job_id)


def latest_job(location_id: str | None = None) -> BackfillJob | None:
    """The running job, else the most recent one (optionally per location)."""
    if _active_job_id and (job := _jobs.get(_active_job_id)):
        if location_id is None or job.location_id == location_id:
            return job
    for job in reversed(_jobs.values()):
        if location_id is None or job.location_id == location_id:
            return job
    return None


def _forget_old_jobs() -> None:
    finished = [job_id for job_id, job in _jobs.items() if not job.active]
    for job_id in finished[: max(0, len(finished) - _MAX_FINISHED_JOBS)]:
        del _jobs[job_id]


def _list_pending(location_id: str | None) -> tuple[str, list[str]]:
    """Resolve the location and list its unanswered review ids (blocking).

    Uses the same location resolution and "unanswered" rule (no business
    reply on Google) as GET /reviews.
    """
    client = get_google_client()
    location_name = get_location_name(client, location_id)
    reviews = get_unanswered_reviews(client, location_name)
    review_ids = [review["reviewId"] for review in reviews if review.get("reviewId")]
    return location_name.rsplit("/", 1)[-1], review_ids


# --- Start / cancel -----------------------------------------------------------------
async def start_backfill(location_id: str | None) -> BackfillJob:
    """Create a job for every unanswered review and start processing it.

    Raises :class:`BackfillRejected` when automation is off, the location is
    not allowlisted, or another backfill is running. Google / OAuth errors
    from listing the reviews propagate unchanged.
    """
    global _active_job_id
    if not settings.auto_reply_enabled:
        raise BackfillRejected(
            "Automatic replies are disabled (AUTO_REPLY_ENABLED=false).", 409
        )
    if location_id and not _location_allowed(location_id):
        raise BackfillRejected(
            "This location is not in AUTO_REPLY_LOCATION_IDS.", 403
        )
    if _active_job_id and (running := _jobs.get(_active_job_id)) and running.active:
        raise BackfillRejected("A backfill job is already running.", 409, running.job_id)

    # Reserve the single slot BEFORE awaiting anything, so two concurrent
    # start requests cannot both pass the check above.
    job = BackfillJob(location_id=location_id, dry_run=settings.auto_reply_dry_run)
    _jobs[job.job_id] = job
    _active_job_id = job.job_id
    try:
        job.location_id, review_ids = await asyncio.to_thread(_list_pending, location_id)
        if not _location_allowed(job.location_id):
            raise BackfillRejected("This location is not in AUTO_REPLY_LOCATION_IDS.", 403)
    except BaseException:
        _jobs.pop(job.job_id, None)
        _active_job_id = None
        raise

    job.items = [BackfillItem(review_id=review_id) for review_id in review_ids]
    logger.info(
        "BACKFILL_STARTED job=%s total=%d location=%s dry_run=%s",
        job.job_id, len(job.items), job.location_id, job.dry_run,
    )
    if not job.items:
        _finish(job, BackfillJobStatus.COMPLETED)
        return job

    task = asyncio.create_task(_run_job(job))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job


def cancel_backfill(job_id: str) -> BackfillJob | None:
    """Stop starting new reviews. A review already in progress finishes."""
    job = _jobs.get(job_id)
    if job is not None and job.active and not job.cancel_event.is_set():
        job.cancel_event.set()
        logger.info("BACKFILL_CANCEL_REQUESTED job=%s", job.job_id)
    return job


# --- Background processing ------------------------------------------------------------
async def _pause(job: BackfillJob, seconds: float) -> None:
    """Sleep, but wake immediately when the job is cancelled."""
    if seconds <= 0:
        return
    try:
        await asyncio.wait_for(job.cancel_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def _process_item(job: BackfillJob, item: BackfillItem) -> None:
    """Run the shared pipeline for one review, retrying retryable failures.

    The processor counts retryable failures per review and marks the run
    non-retryable once MAX_PROCESSING_ATTEMPTS is reached, so this loop is
    bounded twice over. No reply is ever published twice: every attempt
    starts with a fresh Google fetch and "already replied" check.
    """
    retry_delay = settings.automation_backfill_retry_delay_seconds
    item.started_at = _now()
    item.outcome = BackfillOutcome.PROCESSING
    logger.info("BACKFILL job=%s review=%s status=PROCESSING", job.job_id, item.review_id)
    # The user explicitly asked to reply to every pending review: earlier
    # outcomes (failed validation, exhausted retries, a dry run) must not make
    # this review a DUPLICATE skip on every later "reply to all".
    forget_review(item.review_id)
    for attempt in range(1, MAX_PROCESSING_ATTEMPTS + 1):
        item.attempts = attempt
        run = await process_new_review(item.review_id, job.location_id)
        item.record(run)
        if not run.retryable or attempt == MAX_PROCESSING_ATTEMPTS or job.cancel_event.is_set():
            break
        logger.info(
            "BACKFILL job=%s review=%s attempt=%d status=%s stage=%s retrying",
            job.job_id, item.review_id, attempt, run.status.value, run.error_stage,
        )
        await _pause(job, retry_delay * attempt)
        if job.cancel_event.is_set():
            break
    item.finished_at = _now()
    if item.outcome is BackfillOutcome.FAILED:
        logger.warning(
            "BACKFILL job=%s review=%s run=%s status=FAILED final_status=%s stage=%s "
            "attempts=%d",
            job.job_id, item.review_id, item.run_id, item.final_status, item.error_stage,
            item.attempts,
        )
    else:
        logger.info(
            "BACKFILL job=%s review=%s run=%s status=%s",
            job.job_id, item.review_id, item.run_id,
            item.final_status if item.outcome is BackfillOutcome.SKIPPED else item.outcome.value,
        )


async def _run_job(job: BackfillJob) -> None:
    job.status = BackfillJobStatus.RUNNING
    try:
        for index, item in enumerate(job.items):
            if job.cancel_event.is_set():
                break
            job.current_review_id = item.review_id
            try:
                await _process_item(job, item)
            except Exception as exc:  # defensive: one review never stops the batch
                logger.exception("BACKFILL job=%s review=%s crashed", job.job_id, item.review_id)
                item.outcome = BackfillOutcome.FAILED
                item.error_stage = item.error_stage or "unexpected"
                item.error = f"{type(exc).__name__}: {exc}"[:500]
                item.finished_at = _now()
            job.current_review_id = None
            if index < len(job.items) - 1:
                await _pause(job, settings.automation_backfill_delay_seconds)
    except asyncio.CancelledError:
        _finish(job, BackfillJobStatus.CANCELLED)
        raise
    except Exception as exc:
        logger.exception("BACKFILL job=%s crashed", job.job_id)
        job.error = f"{type(exc).__name__}: {exc}"[:500]
        _finish(job, BackfillJobStatus.FAILED)
        return
    _finish(
        job,
        BackfillJobStatus.CANCELLED if job.cancel_event.is_set() else BackfillJobStatus.COMPLETED,
    )


def _finish(job: BackfillJob, status: BackfillJobStatus) -> None:
    global _active_job_id
    for item in job.items:
        if item.outcome in (BackfillOutcome.PENDING, BackfillOutcome.PROCESSING):
            item.outcome = BackfillOutcome.CANCELLED
    job.status = status
    job.current_review_id = None
    job.finished_at = _now()
    if _active_job_id == job.job_id:
        _active_job_id = None
    _forget_old_jobs()
    summary = job.summary()
    logger.info(
        "BACKFILL_%s job=%s total=%d published=%d would_publish=%d skipped=%d "
        "failed=%d cancelled=%d",
        status.value, job.job_id, summary["total"], summary["published"],
        summary["would_publish"], summary["skipped"], summary["failed"],
        summary["cancelled"],
    )


async def wait_for_job(job_id: str, timeout: float | None = None) -> BackfillJob | None:
    """Wait until a job finishes (tests and scripts)."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while (job := _jobs.get(job_id)) is not None and job.active:
        if deadline is not None and time.monotonic() > deadline:
            break
        await asyncio.sleep(0.01)
    return _jobs.get(job_id)
