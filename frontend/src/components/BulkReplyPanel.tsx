import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Info,
  Loader2,
  X,
} from 'lucide-react'
import { ApiError, api } from '../lib/api'
import type { AutomationStatus, BackfillItem, BackfillJob } from '../lib/types'
import { classNames } from '../lib/utils'

// Bulk "Reply to All Pending Reviews". The backend runs every pending review
// through the same pipeline as the real-time automation (one at a time); this
// panel only starts the job, polls its progress and shows the result.

export const DEFAULT_POLL_INTERVAL_MS = 3000
/** Consecutive polling failures before the panel stops and asks to retry. */
const MAX_POLL_FAILURES = 5
const ADMIN_KEY_STORAGE = 'rra.automationKey'
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
  if (err.status === 403 && err.detail.includes('admin key')) return 'The admin key is incorrect.'
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
  onAuthExpired: () => void
  pollIntervalMs?: number
}

export function BulkReplyPanel({
  locationId,
  pendingCount,
  onFinished,
  onAuthExpired,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
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
  const [adminKey, setAdminKey] = useState(() => readStorage(ADMIN_KEY_STORAGE) ?? '')
  const [cancelling, setCancelling] = useState(false)
  const notifiedJobs = useRef(new Set<string>())
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
    const key = adminKey.trim()
    if (!key) {
      setStartError('Enter the admin key to start the bulk reply.')
      return
    }
    setStarting(true)
    setStartError(null)
    try {
      const result = await api.startBackfill(locationId, key)
      writeStorage(ADMIN_KEY_STORAGE, key)
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
        if (err instanceof ApiError && err.status === 403) writeStorage(ADMIN_KEY_STORAGE, null)
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
      setJob(await api.cancelBackfill(job.job_id, readStorage(ADMIN_KEY_STORAGE) ?? ''))
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

  if (job && isActive(job)) {
    return (
      <ProgressCard
        job={job}
        connectionLost={pollFailures >= MAX_POLL_FAILURES}
        onRetry={() => setPollFailures(0)}
        onCancel={readStorage(ADMIN_KEY_STORAGE) ? handleCancel : undefined}
        cancelling={cancelling}
        notice={notice}
      />
    )
  }
  if (job) return <ResultCard job={job} onDismiss={dismissResult} />

  const disabledReason = automationError
    ? 'Could not load the automation settings.'
    : !automation
      ? null
      : !automation.enabled
        ? 'Automatic replies are disabled on the server (AUTO_REPLY_ENABLED=false).'
        : !automation.backfill_configured
          ? 'Bulk reply is not configured on the server (AUTOMATION_BACKFILL_KEY is not set).'
          : null
  const disabled = !automation || !!disabledReason || pendingCount === 0

  return (
    <div className="card flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-50">
          <Bot className="h-5 w-5 text-brand-600" />
        </div>
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Bulk reply</h2>
          <p className="text-sm text-slate-500">
            {pendingCount === 0
              ? 'There are no pending reviews right now.'
              : 'Generate, check and post replies to every pending review automatically.'}
          </p>
          {interrupted && (
            <p className="mt-1 flex items-center gap-1.5 text-sm text-amber-700">
              <Info className="h-4 w-4" />
              The last bulk reply was interrupted (the server restarted). Replies already
              published are safe; start again to continue with the remaining reviews.
            </p>
          )}
          {notice && <p className="mt-1 text-sm text-slate-600">{notice}</p>}
          {disabledReason && (
            <p className="mt-1 text-sm text-rose-600">
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
      <button
        className="btn-primary shrink-0"
        disabled={disabled}
        onClick={() => {
          setStartError(null)
          setNotice(null)
          setConfirmOpen(true)
        }}
      >
        Reply to All Pending Reviews ({pendingCount})
      </button>

      {confirmOpen && automation && (
        <ConfirmBulkDialog
          pendingCount={pendingCount}
          dryRun={automation.dry_run}
          adminKey={adminKey}
          onAdminKeyChange={setAdminKey}
          error={startError}
          busy={starting}
          onConfirm={handleStart}
          onCancel={() => setConfirmOpen(false)}
        />
      )}
    </div>
  )
}

function ConfirmBulkDialog({
  pendingCount,
  dryRun,
  adminKey,
  onAdminKeyChange,
  error,
  busy,
  onConfirm,
  onCancel,
}: {
  pendingCount: number
  dryRun: boolean
  adminKey: string
  onAdminKeyChange: (value: string) => void
  error: string | null
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && !busy) onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onCancel])

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/50" onClick={() => !busy && onCancel()} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="bulk-reply-title"
        className="relative z-10 w-full max-w-md animate-fade-in rounded-2xl bg-white p-6 shadow-xl"
      >
        <h2 id="bulk-reply-title" className="text-lg font-bold text-slate-900">
          Reply to all pending reviews?
        </h2>
        <p className="mt-2 text-sm text-slate-600">
          {pendingCount} unanswered {pendingCount === 1 ? 'review' : 'reviews'} will be processed
          automatically. Each reply will be generated, validated, and checked before publishing.
        </p>
        {dryRun ? (
          <p className="mt-3 flex gap-2 rounded-lg bg-sky-50 p-3 text-sm text-sky-800">
            <Info className="mt-0.5 h-4 w-4 shrink-0" />
            Dry run is enabled. Replies will be generated but not published.
          </p>
        ) : (
          <p className="mt-3 flex gap-2 rounded-lg bg-rose-50 p-3 text-sm font-semibold text-rose-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            Replies will be published to Google.
          </p>
        )}
        <label className="mt-4 block text-sm font-medium text-slate-700" htmlFor="bulk-admin-key">
          Admin key
        </label>
        <input
          id="bulk-admin-key"
          type="password"
          autoComplete="off"
          className="input mt-1"
          placeholder="AUTOMATION_BACKFILL_KEY"
          value={adminKey}
          onChange={(e) => onAdminKeyChange(e.target.value)}
          disabled={busy}
        />
        {error && (
          <p role="alert" className="mt-3 text-sm text-rose-600">
            {error}
          </p>
        )}
        <div className="mt-6 flex justify-end gap-3">
          <button className="btn-secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="btn-primary" onClick={onConfirm} disabled={busy}>
            {busy ? 'Starting…' : 'Start Bulk Reply'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Counts({ job }: { job: BackfillJob }) {
  const counts: Array<[string, number, string]> = [
    job.dry_run
      ? ['Would publish', job.would_publish, 'text-sky-700']
      : ['Published', job.published, 'text-emerald-700'],
    ['Skipped', job.skipped, 'text-slate-700'],
    ['Failed', job.failed, job.failed ? 'text-rose-700' : 'text-slate-700'],
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
        'rounded-full px-2 py-0.5 text-xs font-semibold',
        dryRun ? 'bg-sky-100 text-sky-700' : 'bg-rose-100 text-rose-700',
      )}
    >
      {dryRun ? 'Dry run' : 'Live'}
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
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
        <div className="h-full rounded-full bg-brand-600 transition-all" style={{ width: `${percent}%` }} />
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
        <p role="alert" className="mt-2 text-sm text-rose-600">
          Lost connection to the server. The bulk reply keeps running there.{' '}
          <button className="font-semibold underline" onClick={onRetry}>
            Check again
          </button>
        </p>
      )}
    </div>
  )
}

function ResultCard({ job, onDismiss }: { job: BackfillJob; onDismiss: () => void }) {
  const [showFailed, setShowFailed] = useState(false)
  const failed = job.items.filter((item) => item.outcome === 'FAILED')
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
              job.status === 'COMPLETED' ? 'text-emerald-600' : 'text-amber-600',
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
      {failed.length > 0 && (
        <div className="mt-3">
          <button
            className="flex items-center gap-1 text-sm font-semibold text-rose-700"
            onClick={() => setShowFailed((v) => !v)}
          >
            {showFailed ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            {showFailed ? 'Hide' : 'Show'} failed reviews ({failed.length})
          </button>
          {showFailed && (
            <ul className="mt-2 space-y-1.5 text-sm text-slate-600">
              {failed.map((item) => (
                <li key={item.review_id}>
                  <Link
                    className="font-medium text-brand-600 hover:underline"
                    to={`/reviews/${encodeURIComponent(item.review_id)}`}
                  >
                    Review {shortId(item.review_id)}
                  </Link>{' '}
                  — {failureReason(item)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
