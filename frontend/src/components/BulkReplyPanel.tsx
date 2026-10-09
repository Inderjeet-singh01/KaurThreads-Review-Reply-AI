import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Info,
  Layers,
  Loader2,
  X,
} from 'lucide-react'
import { ApiError, api } from '../lib/api'
import type { AutomationStatus, BackfillItem, BackfillJob } from '../lib/types'
import { classNames } from '../lib/utils'
import { Modal } from './Modal'

// Bulk "Reply to All Pending Reviews". The backend runs every pending review
// through the same pipeline as the real-time automation (one at a time); this
// panel only starts the job, polls its progress and shows the result.

export const DEFAULT_POLL_INTERVAL_MS = 3000
/** Consecutive polling failures before the panel stops and asks to retry. */
const MAX_POLL_FAILURES = 5
const jobStorageKey = (locationId: string | null) => `rra.backfillJob.${locationId ?? 'default'}`

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* storage unavailable — in-memory only */
  }
}

const isActive = (job: BackfillJob | null) =>
  !!job && (job.status === 'STARTING' || job.status === 'RUNNING')

/** User-facing message for a failed backfill request (never raw internals). */
function backfillErrorMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return 'Something went wrong. Please try again.'
  if (err.status === 0) return err.message
  if (err.status === 401) return 'Your Google session has expired. Please reconnect your account.'
  if ([403, 409, 503].includes(err.status) && err.detail) return err.detail
  return err.message
}

const STAGE_LABELS: Record<string, string> = {
  fetch: 'fetching the review from Google failed',
  initial_check: 'checking the review on Google failed',
  generation: 'AI reply generation failed',
  regeneration: 'AI reply regeneration failed',
  validation: 'the reply did not pass validation',
  revalidation: 'the reply did not pass validation',
  final_check: 'the final Google check failed',
  publish: 'publishing to Google failed',
  unexpected: 'an unexpected error occurred',
}

const shortId = (id: string) => (id.length > 12 ? `${id.slice(0, 10)}…` : id)

const SKIP_LABELS: Record<string, string> = {
  SKIPPED_ALREADY_REPLIED: 'already answered on Google',
  SKIPPED_NOT_FOUND: 'no longer on Google',
  DISABLED: 'automatic replies were turned off',
  IGNORED: 'location not enabled for automatic replies',
}

function skipReason(item: BackfillItem): string {
  return (
    (item.final_status && SKIP_LABELS[item.final_status]) ||
    (item.error ?? '').replace(/^[A-Za-z]+(Error|Exception): /, '').slice(0, 160) ||
    'skipped'
  )
}

function failureReason(item: BackfillItem): string {
  const stage = item.error_stage ? STAGE_LABELS[item.error_stage] ?? item.error_stage : ''
  // Backend errors are secret-free; drop the exception class prefix and trim.
  const detail = (item.error ?? '').replace(/^[A-Za-z]+(Error|Exception): /, '').slice(0, 160)
  return [stage, detail].filter(Boolean).join(' — ') || 'failed'
}

interface BulkReplyPanelProps {
  locationId: string | null
  /** Unanswered reviews currently shown (from the backend review list). */
  pendingCount: number
  /** Called once when a job finishes, so the review list can refresh. */
  onFinished: () => void
  /**
   * Called with the ids of reviews that were just published, while the job
   * is still running, so counts and lists can update review by review.
   */
  onReviewsPublished?: (reviewIds: string[]) => void
  /** Human label (e.g. reviewer name) for a review id in the result lists. */
  reviewLabel?: (reviewId: string) => string | undefined
  onAuthExpired: () => void
  pollIntervalMs?: number
  /** Open the confirmation as soon as a bulk reply can be started (once). */
  autoOpen?: boolean
}

export function BulkReplyPanel({
  locationId,
  pendingCount,
  onFinished,
  onReviewsPublished,
  reviewLabel,
  onAuthExpired,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  autoOpen = false,
}: BulkReplyPanelProps) {
  const [automation, setAutomation] = useState<AutomationStatus | null>(null)
  const [automationError, setAutomationError] = useState(false)
  const [job, setJob] = useState<BackfillJob | null>(null)
  const [interrupted, setInterrupted] = useState(false)
  const [pollFailures, setPollFailures] = useState(0)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const notifiedJobs = useRef(new Set<string>())
  const reportedPublished = useRef(new Set<string>())
  const autoOpened = useRef(false)
  const storageKey = jobStorageKey(locationId)

  const loadAutomation = useCallback(() => {
    setAutomationError(false)
    api
      .getAutomationStatus()
      .then(setAutomation)
      .catch(() => setAutomationError(true))
  }, [])

  useEffect(loadAutomation, [loadAutomation])

  // Recover a job after a page refresh: the stored job id first, otherwise
  // whatever the server is running for this location right now.
  useEffect(() => {
    let cancelled = false
    setJob(null)
    setInterrupted(false)
    const storedId = readStorage(storageKey)
    const recover = storedId
      ? api.getBackfill(storedId).catch((err) => {
          if (err instanceof ApiError && err.status === 404) {
            writeStorage(storageKey, null)
            if (!cancelled) setInterrupted(true)
          }
          return null
        })
      : api
          .getLatestBackfill(locationId)
          .then(({ job: latest }) => (isActive(latest) ? latest : null))
          .catch(() => null)
    void recover.then((recovered) => {
      if (cancelled || !recovered) return
      writeStorage(storageKey, recovered.job_id)
      if (!isActive(recovered)) notifiedJobs.current.add(recovered.job_id)
      setJob(recovered)
    })
    return () => {
      cancelled = true
    }
  }, [locationId, storageKey])

  // Poll while the job runs; stop once it finishes or polling keeps failing.
  useEffect(() => {
    if (!job || !isActive(job) || pollFailures >= MAX_POLL_FAILURES) return
    const timer = window.setTimeout(() => {
      api
        .getBackfill(job.job_id)
        .then((next) => {
          setPollFailures(0)
          setJob(next)
        })
        .catch((err) => {
          if (err instanceof ApiError && err.status === 404) {
            // The server restarted and forgot the job.
            writeStorage(storageKey, null)
            setJob(null)
            setInterrupted(true)
            return
          }
          setPollFailures((n) => n + 1)
        })
    }, pollIntervalMs)
    return () => window.clearTimeout(timer)
  }, [job, pollFailures, pollIntervalMs, storageKey])

  // Report reviews as soon as the job publishes them (not only at the end).
  useEffect(() => {
    if (!job || !onReviewsPublished) return
    const fresh = job.items
      .filter((item) => item.outcome === 'PUBLISHED' && !reportedPublished.current.has(item.review_id))
      .map((item) => item.review_id)
    if (fresh.length === 0) return
    fresh.forEach((id) => reportedPublished.current.add(id))
    onReviewsPublished(fresh)
  }, [job, onReviewsPublished])

  // Refresh the review list once per finished job.
  useEffect(() => {
    if (job && !isActive(job) && !notifiedJobs.current.has(job.job_id)) {
      notifiedJobs.current.add(job.job_id)
      onFinished()
    }
  }, [job, onFinished])

  const showJob = useCallback(
    async (jobId: string) => {
      writeStorage(storageKey, jobId)
      setPollFailures(0)
      try {
        setJob(await api.getBackfill(jobId))
      } catch {
        // Polling picks it up; show a placeholder meanwhile.
        setPollFailures(1)
      }
    },
    [storageKey],
  )

  const handleStart = async () => {
    setStarting(true)
    setStartError(null)
    try {
      const result = await api.startBackfill(locationId)
      setConfirmOpen(false)
      setInterrupted(false)
      if (result.started && result.job_id) {
        await showJob(result.job_id)
      } else {
        setNotice(result.reason ?? 'There are no pending reviews to reply to.')
        onFinished()
      }
    } catch (err) {
      const running = (err as ApiError)?.body as { job_id?: string } | undefined
      if (err instanceof ApiError && err.status === 409 && running?.job_id) {
        setConfirmOpen(false)
        setNotice('A bulk reply is already running — showing its progress.')
        await showJob(running.job_id)
      } else if (err instanceof ApiError && err.isAuth) {
        setConfirmOpen(false)
        onAuthExpired()
      } else {
        setStartError(backfillErrorMessage(err))
      }
    } finally {
      setStarting(false)
    }
  }

  const handleCancel = async () => {
    if (!job) return
    setCancelling(true)
    try {
      setJob(await api.cancelBackfill(job.job_id))
    } catch (err) {
      setNotice(`Could not stop the bulk reply: ${backfillErrorMessage(err)}`)
    } finally {
      setCancelling(false)
    }
  }

  const dismissResult = () => {
    writeStorage(storageKey, null)
    setJob(null)
  }

  const disabledReason = automationError
    ? 'Could not load the automation settings.'
    : !automation
      ? null
      : !automation.enabled
        ? 'Automatic replies are disabled on the server (AUTO_REPLY_ENABLED=false).'
        : null
  const disabled = !automation || !!disabledReason || pendingCount === 0

  const openConfirm = () => {
    setStartError(null)
    setNotice(null)
    setConfirmOpen(true)
  }

  // "Bulk reply" quick action elsewhere: ask for confirmation right away
  // (never start without it).
  useEffect(() => {
    if (!autoOpen || autoOpened.current || disabled || isActive(job)) return
    autoOpened.current = true
    openConfirm()
    // openConfirm only resets messages and opens the dialog.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoOpen, disabled, job])

  const confirmDialog = automation && (
    <ConfirmBulkDialog
      open={confirmOpen}
      pendingCount={pendingCount}
      dryRun={automation.dry_run}
      error={startError}
      busy={starting}
      onConfirm={handleStart}
      onCancel={() => setConfirmOpen(false)}
    />
  )

  if (job && isActive(job)) {
    return (
      <ProgressCard
        job={job}
        connectionLost={pollFailures >= MAX_POLL_FAILURES}
        onRetry={() => setPollFailures(0)}
        onCancel={handleCancel}
        cancelling={cancelling}
        notice={notice}
      />
    )
  }
  if (job) {
    return (
      <>
        <ResultCard
          job={job}
          onDismiss={dismissResult}
          reviewLabel={reviewLabel}
          pendingCount={pendingCount}
          onRunAgain={disabled ? undefined : openConfirm}
        />
        {confirmDialog}
      </>
    )
  }

  return (
    <div className="card flex flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-5">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50" aria-hidden>
          <Layers className="h-[18px] w-[18px] text-brand-600" />
        </div>
        <div className="min-w-0">
          <h2 className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
            Bulk reply
            {automation && automation.enabled && <ModeBadge dryRun={automation.dry_run} />}
          </h2>
          <p className="text-[13px] text-ink-muted">
            {pendingCount === 0
              ? 'There are no pending reviews right now.'
              : 'Generate, check and post replies to every pending review in one run.'}
          </p>
          {interrupted && (
            <p className="mt-1 flex items-start gap-1.5 text-[13px] text-amber-800">
              <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              The last bulk reply was interrupted (the server restarted). Replies already
              published are safe; start again to continue with the remaining reviews.
            </p>
          )}
          {notice && <p className="mt-1 text-[13px] text-slate-700">{notice}</p>}
          {disabledReason && (
            <p className="mt-1 text-[13px] text-red-700">
              {disabledReason}{' '}
              {automationError && (
                <button className="font-semibold underline" onClick={loadAutomation}>
                  Try again
                </button>
              )}
            </p>
          )}
        </div>
      </div>
      <button className="btn-primary shrink-0" disabled={disabled} onClick={openConfirm}>
        Reply to All Pending Reviews ({pendingCount})
      </button>

      {confirmDialog}
    </div>
  )
}

function ConfirmBulkDialog({
  open,
  pendingCount,
  dryRun,
  error,
  busy,
  onConfirm,
  onCancel,
}: {
  open: boolean
  pendingCount: number
  dryRun: boolean
  error: string | null
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const titleId = useId()
  return (
    <Modal open={open} onClose={onCancel} labelledBy={titleId} busy={busy} size="md">
      <div className="overflow-y-auto p-6">
        <h2 id={titleId} className="text-lg font-semibold text-ink">
          Reply to all pending reviews?
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-600">
          {pendingCount} unanswered {pendingCount === 1 ? 'review' : 'reviews'} will be processed
          automatically. Each reply will be generated, validated, and checked before publishing.
        </p>
        <ol className="mt-4 space-y-2 rounded-lg border border-line bg-slate-50 p-4 text-[13px] text-slate-700">
          {[
            'AI drafts a reply for each review, one at a time on the server.',
            'Each draft is checked; a draft that fails is regenerated or skipped.',
            'Google is checked again right before publishing — reviews answered meanwhile are skipped.',
          ].map((step, i) => (
            <li key={step} className="flex gap-2.5">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white text-[11px] font-semibold text-brand-700 ring-1 ring-brand-100">
                {i + 1}
              </span>
              {step}
            </li>
          ))}
        </ol>
        {dryRun ? (
          <p className="mt-4 flex gap-2 rounded-lg bg-sky-50 p-3 text-sm text-sky-900">
            <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            Dry run is enabled. Replies will be generated but not published.
          </p>
        ) : (
          <p className="mt-4 flex gap-2 rounded-lg bg-amber-50 p-3 text-sm font-semibold text-amber-900">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            Replies will be published to Google.
          </p>
        )}
        <p className="mt-3 text-[13px] text-ink-muted">
          You can stop the run after the current review. This one-time job is separate from
          automatic replies to new reviews.
        </p>
        {error && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            {error}
          </p>
        )}
      </div>
      <div className="flex flex-col-reverse gap-2 border-t border-line bg-slate-50 px-6 py-4 sm:flex-row sm:justify-end sm:gap-3">
        <button className="btn-secondary" onClick={onCancel} disabled={busy} data-autofocus>
          Cancel
        </button>
        <button className="btn-primary" onClick={onConfirm} disabled={busy}>
          {busy ? 'Starting…' : 'Start Bulk Reply'}
        </button>
      </div>
    </Modal>
  )
}

function Counts({ job }: { job: BackfillJob }) {
  const counts: Array<[string, number, string]> = [
    job.dry_run
      ? ['Would publish', job.would_publish, 'text-sky-800']
      : ['Published', job.published, 'text-green-700'],
    ['Skipped', job.skipped, 'text-slate-700'],
    ['Failed', job.failed, job.failed ? 'text-red-700' : 'text-slate-700'],
  ]
  return (
    <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm">
      {counts.map(([label, value, color]) => (
        <div key={label} className="flex gap-1.5">
          <dt className="text-slate-500">{label}:</dt>
          <dd className={classNames('font-semibold', color)}>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function ModeBadge({ dryRun }: { dryRun: boolean }) {
  return (
    <span
      className={classNames(
        'rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset',
        dryRun ? 'bg-sky-50 text-sky-800 ring-sky-100' : 'bg-amber-50 text-amber-800 ring-amber-100',
      )}
    >
      {dryRun ? 'Dry run' : 'Live publishing'}
    </span>
  )
}

function ProgressCard({
  job,
  connectionLost,
  onRetry,
  onCancel,
  cancelling,
  notice,
}: {
  job: BackfillJob
  connectionLost: boolean
  onRetry: () => void
  onCancel?: () => void
  cancelling: boolean
  notice: string | null
}) {
  const percent = job.total ? Math.round((job.processed / job.total) * 100) : 0
  return (
    <div className="card p-5" aria-live="polite">
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
          <Loader2 className="h-4 w-4 animate-spin text-brand-600" />
          Bulk Reply in Progress
          <ModeBadge dryRun={job.dry_run} />
        </h2>
        {onCancel && (
          <button
            className="btn-ghost text-sm"
            onClick={onCancel}
            disabled={cancelling || job.cancel_requested}
          >
            {job.cancel_requested ? 'Stopping…' : 'Stop after current review'}
          </button>
        )}
      </div>
      {notice && <p className="mt-1 text-sm text-slate-600">{notice}</p>}
      <p className="mt-3 text-sm font-medium text-slate-700">
        {job.processed} / {job.total} processed
      </p>
      <div
        className="mt-2 h-2 overflow-hidden rounded-full bg-brand-50"
        role="progressbar"
        aria-label="Bulk reply progress"
        aria-valuemin={0}
        aria-valuemax={job.total}
        aria-valuenow={job.processed}
      >
        <div className="h-full rounded-full bg-brand-600 transition-[width] duration-300" style={{ width: `${percent}%` }} />
      </div>
      <Counts job={job} />
      <p className="mt-2 text-sm text-slate-500">
        Current:{' '}
        {job.cancel_requested
          ? 'Stopping after the current review…'
          : job.current_review_id
            ? 'Processing review…'
            : 'Waiting before the next review…'}
      </p>
      {connectionLost && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          Lost connection to the server. The bulk reply keeps running there.{' '}
          <button className="font-semibold underline" onClick={onRetry}>
            Check again
          </button>
        </p>
      )}
    </div>
  )
}

function ResultCard({
  job,
  onDismiss,
  reviewLabel,
  pendingCount,
  onRunAgain,
}: {
  job: BackfillJob
  onDismiss: () => void
  reviewLabel?: (reviewId: string) => string | undefined
  pendingCount: number
  /** Start another bulk reply for the reviews still pending (when allowed). */
  onRunAgain?: () => void
}) {
  const failed = job.items.filter((item) => item.outcome === 'FAILED')
  const skipped = job.items.filter((item) => item.outcome === 'SKIPPED')
  const title =
    job.status === 'COMPLETED'
      ? 'Bulk reply completed'
      : job.status === 'CANCELLED'
        ? 'Bulk reply stopped'
        : 'Bulk reply failed'
  const Icon = job.status === 'COMPLETED' ? CheckCircle2 : AlertTriangle
  return (
    <div className="card p-5">
      <div className="flex items-start justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
          <Icon
            className={classNames(
              'h-5 w-5',
              job.status === 'COMPLETED' ? 'text-green-600' : 'text-amber-600',
            )}
          />
          {title}
          <ModeBadge dryRun={job.dry_run} />
        </h2>
        <button className="btn-ghost !p-1.5" onClick={onDismiss} aria-label="Dismiss">
          <X className="h-4 w-4" />
        </button>
      </div>
      {job.status === 'FAILED' && (
        <p className="mt-2 text-sm text-slate-600">
          The bulk reply stopped unexpectedly. Replies already published are not affected.
        </p>
      )}
      {job.status === 'CANCELLED' && job.cancelled > 0 && (
        <p className="mt-2 text-sm text-slate-600">
          {job.cancelled} {job.cancelled === 1 ? 'review was' : 'reviews were'} not started.
        </p>
      )}
      <p className="mt-3 text-sm font-medium text-slate-700">Processed: {job.processed}</p>
      <Counts job={job} />
      <ItemList
        label="failed"
        tone="text-red-700"
        items={failed}
        reason={failureReason}
        reviewLabel={reviewLabel}
      />
      <ItemList
        label="skipped"
        tone="text-slate-700"
        items={skipped}
        reason={skipReason}
        reviewLabel={reviewLabel}
      />
      {onRunAgain && pendingCount > 0 && (
        <div className="mt-4 flex flex-col gap-2 border-t border-line pt-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-slate-600">
            {pendingCount} {pendingCount === 1 ? 'review is' : 'reviews are'} still unanswered.
          </p>
          <button className="btn-primary shrink-0" onClick={onRunAgain}>
            Reply to All Pending Reviews ({pendingCount})
          </button>
        </div>
      )}
    </div>
  )
}

function ItemList({
  label,
  tone,
  items,
  reason,
  reviewLabel,
}: {
  label: string
  tone: string
  items: BackfillItem[]
  reason: (item: BackfillItem) => string
  reviewLabel?: (reviewId: string) => string | undefined
}) {
  const [open, setOpen] = useState(false)
  if (items.length === 0) return null
  return (
    <div className="mt-3">
      <button
        className={classNames('flex items-center gap-1 text-sm font-semibold', tone)}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        {open ? 'Hide' : 'Show'} {label} reviews ({items.length})
      </button>
      {open && (
        <ul className="mt-2 space-y-1.5 text-sm text-slate-600">
          {items.map((item) => (
            <li key={item.review_id}>
              <Link
                className="font-medium text-brand-600 hover:underline"
                to={`/reviews/${encodeURIComponent(item.review_id)}`}
              >
                {reviewLabel?.(item.review_id) ?? `Review ${shortId(item.review_id)}`}
              </Link>{' '}
              — {reason(item)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
