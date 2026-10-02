"""Reconciliation: the safety net for delayed or missed Pub/Sub notifications.

Real-time replies depend on Pub/Sub delivering every NEW_REVIEW. A
notification can be late (Render asleep or restarting), exhausted (retry
budget / dead-letter), or malformed beyond recovery. Reconciliation closes
that gap without any long-running loop inside the web process: an external
scheduler calls ``POST /automation/reconcile`` every few minutes, and each
call is one bounded job::

    start_reconciliation(location)
      ├─ disabled / AUTO_REPLY_ENABLED=false            ──> rejected (409)
      ├─ another reconciliation or a backfill running   ──> rejected (409)
      ├─ list unanswered reviews from Google (same helper as the webhook
      │   fallback): created/updated within RECONCILIATION_LOOKBACK_MINUTES,
      │   newest first
      ├─ drop reviews another run is processing / recently settled
      ├─ keep at most RECONCILIATION_MAX_REVIEWS
      └─ background task, ONE review at a time:
           process_new_review(review, trigger="reconcile")   ← same processor
           a failure never stops the job; pause between reviews

There is no second reply implementation: every review goes through
:func:`app.automation.processor.process_new_review`, so AUTO_REPLY_DRY_RUN,
the location allowlist, at most one regeneration, the retry budget, the
per-review lock and both Google "already replied" checks all apply. Unlike
the user-triggered backfill, reconciliation does NOT clear the processor's
outcome memory: a review that failed validation or exhausted its retries
is not retried on every scheduled run (only after the 24 h memory expires
or a restart).

Job records live in memory (like backfill jobs); a restart loses only the
record — the next scheduled call starts again from Google's state.
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

from app.automation import backfill
from app.automation.backfill import BackfillItem, BackfillOutcome
from app.automation.processor import _location_allowed, process_new_review, review_activity
from app.automation.resolver import list_recent_unanswered
from app.config import settings

logger = logging.getLogger(__name__)

_MAX_FINISHED_JOBS = 20


class ReconcileJobStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"  # the job itself crashed; per-review failures do not count


class ReconcileRejected(Exception):
    """A reconciliation could not be started."""

    def __init__(self, reason: str, status_code: int = 409, job_id: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code
        self.job_id = job_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ReconcileJob:
    trigger: str
    location_id: str | None
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: ReconcileJobStatus = ReconcileJobStatus.RUNNING
    dry_run: bool = False
    lookback_minutes: int = 0
    max_reviews: int = 0
    unanswered_recent: int = 0
    skipped_in_progress: list[str] = field(default_factory=list)
    skipped_recently_settled: list[str] = field(default_factory=list)
    deferred: int = 0  # beyond max_reviews; picked up by the next run
    items: list[BackfillItem] = field(default_factory=list)
    current_review_id: str | None = None
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    error: str | None = None

    @property
    def active(self) -> bool:
        return self.status is ReconcileJobStatus.RUNNING

    def _count(self, *outcomes: BackfillOutcome) -> int:
        return sum(1 for item in self.items if item.outcome in outcomes)

    def summary(self, include_items: bool = True) -> dict[str, Any]:
        # Never includes reply text, tokens or credentials.
        data: dict[str, Any] = {
            "job_id": self.job_id,
            "status": self.status.value,
            "trigger": self.trigger,
            "location_id": self.location_id,
            "dry_run": self.dry_run,
            "lookback_minutes": self.lookback_minutes,
            "max_reviews": self.max_reviews,
            "unanswered_recent": self.unanswered_recent,
            "total": len(self.items),
            "processed": self._count(
                BackfillOutcome.PUBLISHED, BackfillOutcome.DRY_RUN,
                BackfillOutcome.SKIPPED, BackfillOutcome.FAILED,
            ),
            "published": self._count(BackfillOutcome.PUBLISHED),
            "would_publish": self._count(BackfillOutcome.DRY_RUN),
            "skipped": self._count(BackfillOutcome.SKIPPED),
            "failed": self._count(BackfillOutcome.FAILED),
            "skipped_in_progress": len(self.skipped_in_progress),
            "skipped_recently_settled": len(self.skipped_recently_settled),
            "deferred": self.deferred,
            "current_review_id": self.current_review_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }
        if include_items:
            data["items"] = [item.summary() for item in self.items]
        return data


# --- In-process job registry -----------------------------------------------------------
_jobs: dict[str, ReconcileJob] = {}
_active_job_id: str | None = None
_tasks: set[asyncio.Task] = set()


def reset_state() -> None:
    """Forget all reconciliation jobs (tests only)."""
    global _active_job_id
    for task in _tasks:
        task.cancel()
    _tasks.clear()
    _jobs.clear()
    _active_job_id = None


def get_job(job_id: str) -> ReconcileJob | None:
    return _jobs.get(job_id)


def latest_job() -> ReconcileJob | None:
    if _active_job_id and (job := _jobs.get(_active_job_id)):
        return job
    return next(reversed(_jobs.values()), None)


def _forget_old_jobs() -> None:
    finished = [job_id for job_id, job in _jobs.items() if not job.active]
    for job_id in finished[: max(0, len(finished) - _MAX_FINISHED_JOBS)]:
        del _jobs[job_id]


# --- Start ---------------------------------------------------------------------------
async def start_reconciliation(
    location_id: str | None = None, trigger: str = "scheduler"
) -> ReconcileJob:
    """Select recent unanswered reviews and process them in the background.

    Raises :class:`ReconcileRejected` when reconciliation or automation is
    off, the location is not allowlisted, or a reconciliation / backfill is
    already running. Google / OAuth errors from listing the reviews
    propagate unchanged.
    """
    global _active_job_id
    if not settings.reconciliation_enabled:
        raise ReconcileRejected("Reconciliation is disabled (RECONCILIATION_ENABLED=false).")
    if not settings.auto_reply_enabled:
        raise ReconcileRejected("Automatic replies are disabled (AUTO_REPLY_ENABLED=false).")
    if location_id and not _location_allowed(location_id):
        raise ReconcileRejected("This location is not in AUTO_REPLY_LOCATION_IDS.", 403)
    if _active_job_id and (running := _jobs.get(_active_job_id)) and running.active:
        raise ReconcileRejected("A reconciliation is already running.", 409, running.job_id)
    if (running_backfill := backfill.active_job()) is not None:
        # The backfill already covers every unanswered review.
        raise ReconcileRejected(
            "A backfill job is running; it already covers the pending reviews.",
            409, running_backfill.job_id,
        )

    # Reserve the single slot BEFORE awaiting anything.
    job = ReconcileJob(
        trigger=trigger,
        location_id=location_id,
        dry_run=settings.auto_reply_dry_run,
        lookback_minutes=settings.reconciliation_lookback_minutes,
        max_reviews=settings.reconciliation_max_reviews,
    )
    _jobs[job.job_id] = job
    _active_job_id = job.job_id
    try:
        job.location_id, candidates = await asyncio.to_thread(list_recent_unanswered, location_id)
        if not _location_allowed(job.location_id):
            raise ReconcileRejected("This location is not in AUTO_REPLY_LOCATION_IDS.", 403)
    except BaseException:
        _jobs.pop(job.job_id, None)
        _active_job_id = None
        raise

    job.unanswered_recent = len(candidates)
    selected: list[str] = []
    for candidate in candidates:
        state = review_activity(candidate.review_id)
        if state == "in_progress":
            job.skipped_in_progress.append(candidate.review_id)
        elif state == "recently_settled":
            job.skipped_recently_settled.append(candidate.review_id)
        else:
            selected.append(candidate.review_id)
    job.deferred = max(0, len(selected) - job.max_reviews)
    job.items = [BackfillItem(review_id=rid) for rid in selected[: job.max_reviews]]
    logger.info(
        "RECONCILE_STARTED job=%s trigger=%s location=%s dry_run=%s unanswered_recent=%d "
        "selected=%d in_progress=%s recently_settled=%s deferred=%d lookback_minutes=%d",
        job.job_id, trigger, job.location_id, job.dry_run, job.unanswered_recent,
        len(job.items), job.skipped_in_progress, job.skipped_recently_settled,
        job.deferred, job.lookback_minutes,
    )
    if not job.items:
        _finish(job, ReconcileJobStatus.COMPLETED)
        return job

    task = asyncio.create_task(_run_job(job))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job


async def reconcile_unanswered_reviews(
    location_id: str | None = None,
    trigger: str = "manual",
    timeout: float | None = None,
) -> ReconcileJob:
    """Run one reconciliation and wait for it (up to ``timeout`` seconds)."""
    job = await start_reconciliation(location_id, trigger)
    return await wait_for_job(job.job_id, timeout) or job


# --- Background processing --------------------------------------------------------------
async def _process_item(job: ReconcileJob, item: BackfillItem) -> None:
    item.started_at = _now()
    item.outcome = BackfillOutcome.PROCESSING
    item.attempts = 1
    run = await process_new_review(item.review_id, job.location_id, trigger="reconcile")
    item.record(run)
    item.finished_at = _now()
    log = logger.warning if item.outcome is BackfillOutcome.FAILED else logger.info
    log(
        "RECONCILE job=%s review=%s run=%s outcome=%s final_status=%s stage=%s retryable=%s",
        job.job_id, item.review_id, item.run_id, item.outcome.value, item.final_status,
        item.error_stage, item.retryable,
    )


async def _run_job(job: ReconcileJob) -> None:
    try:
        for index, item in enumerate(job.items):
            job.current_review_id = item.review_id
            try:
                await _process_item(job, item)
            except Exception as exc:  # defensive: one review never stops the job
                logger.exception("RECONCILE job=%s review=%s crashed", job.job_id, item.review_id)
                item.outcome = BackfillOutcome.FAILED
                item.error_stage = item.error_stage or "unexpected"
                item.error = f"{type(exc).__name__}: {exc}"[:500]
                item.finished_at = _now()
            job.current_review_id = None
            if index < len(job.items) - 1 and settings.automation_backfill_delay_seconds > 0:
                await asyncio.sleep(settings.automation_backfill_delay_seconds)
    except asyncio.CancelledError:
        job.error = "cancelled (server shutting down?)"
        _finish(job, ReconcileJobStatus.FAILED)
        raise
    except Exception as exc:
        logger.exception("RECONCILE job=%s crashed", job.job_id)
        job.error = f"{type(exc).__name__}: {exc}"[:500]
        _finish(job, ReconcileJobStatus.FAILED)
        return
    _finish(job, ReconcileJobStatus.COMPLETED)


def _finish(job: ReconcileJob, status: ReconcileJobStatus) -> None:
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
    summary = job.summary(include_items=False)
    logger.info(
        "RECONCILE_%s job=%s trigger=%s total=%d published=%d would_publish=%d skipped=%d "
        "failed=%d in_progress=%d recently_settled=%d deferred=%d",
        status.value, job.job_id, job.trigger, summary["total"], summary["published"],
        summary["would_publish"], summary["skipped"], summary["failed"],
        summary["skipped_in_progress"], summary["skipped_recently_settled"], summary["deferred"],
    )


async def wait_for_job(job_id: str, timeout: float | None = None) -> ReconcileJob | None:
    """Wait until a job finishes (endpoint ``wait=true``, tests, scripts)."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while (job := _jobs.get(job_id)) is not None and job.active:
        if deadline is not None and time.monotonic() > deadline:
            break
        await asyncio.sleep(0.01)
    return _jobs.get(job_id)
