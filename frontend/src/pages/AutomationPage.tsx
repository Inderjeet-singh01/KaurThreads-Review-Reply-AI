import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleSlash,
  History,
  Info,
  Layers,
  Lock,
  RefreshCw,
  XCircle,
} from 'lucide-react'
import { ApiError, api } from '../lib/api'
import type { AutomationStatus, ReconciliationSummary } from '../lib/types'
import { useBusiness } from '../context/BusinessContext'
import { classNames, formatMinutes, formatTimeWithRelative } from '../lib/utils'
import { PageHeader } from '../components/PageHeader'
import { Badge } from '../components/StatusBadge'
import { ErrorState } from '../components/ErrorState'
import { PanelSkeleton } from '../components/Skeletons'

/** Read-only view of the server's automatic-reply configuration and health. */
export function AutomationPage({ onAuthExpired }: { onAuthExpired: () => void }) {
  const { business } = useBusiness()
  const [status, setStatus] = useState<AutomationStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setStatus(await api.getAutomationStatus())
    } catch (err) {
      if (err instanceof ApiError && err.isAuth) {
        onAuthExpired()
        return
      }
      setError(err instanceof ApiError ? err.message : 'Could not load the automation status.')
    } finally {
      setLoading(false)
    }
  }, [onAuthExpired])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="space-y-6">
      <PageHeader
        title="Automation"
        description="How new Google reviews are handled automatically, as configured on the server."
        actions={
          <button
            className="btn-secondary px-3 sm:px-4"
            onClick={() => void load()}
            disabled={loading}
            aria-label="Refresh automation status"
          >
            <RefreshCw className={classNames('h-4 w-4', loading && 'animate-spin')} aria-hidden />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        }
      />

      {loading && !status ? (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <div className="card xl:col-span-2">
            <PanelSkeleton lines={2} />
          </div>
          <div className="card">
            <PanelSkeleton lines={6} />
          </div>
          <div className="card">
            <PanelSkeleton lines={6} />
          </div>
        </div>
      ) : error ? (
        <ErrorState
          title="Couldn’t load the automation status"
          message={error}
          onRetry={() => void load()}
        />
      ) : status ? (
        <AutomationDetails status={status} currentLocationId={business?.location_id ?? null} />
      ) : null}
    </div>
  )
}

function AutomationDetails({
  status,
  currentLocationId,
}: {
  status: AutomationStatus
  currentLocationId: string | null
}) {
  const mode = !status.enabled ? 'off' : status.dry_run ? 'dry-run' : 'live'
  const scoped = status.location_ids.length > 0
  const coversCurrent = !scoped || (currentLocationId != null && status.location_ids.includes(currentLocationId))

  const warnings: string[] = []
  if (status.enabled && !status.webhook_auth_configured)
    warnings.push(
      'Automatic replies are on, but Pub/Sub push authentication is not configured, so the server rejects every new-review notification.',
    )
  if (status.enabled && status.reconciliation_enabled && status.reconciliation_auth_configured === false)
    warnings.push(
      'The reconciliation safety net is enabled but has no scheduler secret, so reviews whose notification was missed are not retried automatically.',
    )
  if (status.enabled && scoped && !coversCurrent)
    warnings.push('The business you are viewing is not in the server’s list of automated locations.')
  if (status.test_endpoint_enabled)
    warnings.push('The development-only test endpoint is enabled on the server. Turn it off in production.')

  const hero = {
    live: {
      icon: Bot,
      iconStyle: 'bg-green-50 text-green-600',
      title: 'Automatic replies are on',
      badge: <Badge tone="success" dot>Live publishing</Badge>,
      text: 'When Google reports a new review, a reply is generated, checked, and published after a final check on Google.',
    },
    'dry-run': {
      icon: Bot,
      iconStyle: 'bg-sky-50 text-sky-600',
      title: 'Automatic replies are in dry-run mode',
      badge: <Badge tone="info" dot>Dry run</Badge>,
      text: 'New reviews go through the full pipeline, but nothing is published. Results are only recorded on the server.',
    },
    off: {
      icon: CircleSlash,
      iconStyle: 'bg-slate-100 text-slate-500',
      title: 'Automatic replies are off',
      badge: <Badge tone="neutral">Off</Badge>,
      text: 'New reviews are only answered manually from the Review Inbox. Notifications are received but never processed by AI.',
    },
  }[mode]
  const HeroIcon = hero.icon

  return (
    <div className="space-y-6">
      <section className="card flex flex-col gap-4 p-5 sm:flex-row sm:items-start sm:p-6" aria-labelledby="automation-state">
        <span className={classNames('flex h-12 w-12 shrink-0 items-center justify-center rounded-xl', hero.iconStyle)} aria-hidden>
          <HeroIcon className="h-6 w-6" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="automation-state" className="text-lg font-semibold text-ink">
              {hero.title}
            </h2>
            {hero.badge}
          </div>
          <p className="mt-1 text-sm leading-relaxed text-slate-600">{hero.text}</p>
        </div>
      </section>

      {warnings.length > 0 && (
        <section role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-4 sm:p-5">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-amber-900">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            Needs attention
          </h2>
          <ul className="mt-2 list-disc space-y-1 pl-6 text-sm text-amber-900">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <section className="card p-5 sm:p-6" aria-labelledby="automation-config">
          <h2 id="automation-config" className="section-title">
            Configuration
          </h2>
          <dl className="mt-4 divide-y divide-line text-sm">
            <Row label="Mode">
              {mode === 'live' ? 'Live publishing' : mode === 'dry-run' ? 'Dry run (nothing published)' : 'Off'}
            </Row>
            <Row label="Locations">
              {scoped ? (
                <span>
                  {status.location_ids.length} selected{' '}
                  <span className="text-ink-muted">
                    ({coversCurrent ? 'includes this business' : 'not this business'})
                  </span>
                </span>
              ) : (
                'All locations of the connected account'
              )}
            </Row>
            <Row label="Regeneration after a failed check">
              {status.max_regenerations === 0 ? 'None' : `Up to ${status.max_regenerations}`}
            </Row>
            <Row label="Attempts per review">Up to {status.max_processing_attempts}</Row>
            {status.verify_after_publish != null && (
              <Row label="Re-read Google after publishing">
                <YesNo value={status.verify_after_publish} />
              </Row>
            )}
            <Row label="Pub/Sub push authentication">
              <YesNo value={status.webhook_auth_configured} yes="Configured" no="Not configured" />
            </Row>
          </dl>
        </section>

        <section className="card p-5 sm:p-6" aria-labelledby="automation-safety">
          <h2 id="automation-safety" className="section-title">
            Safety net (reconciliation)
          </h2>
          <p className="mt-0.5 text-[13px] text-ink-muted">
            A scheduled check that retries recent unanswered reviews whose notification was missed.
          </p>
          {status.reconciliation_enabled == null ? (
            <p className="mt-4 text-sm text-ink-muted">Not reported by this server version.</p>
          ) : (
            <>
              <dl className="mt-4 divide-y divide-line text-sm">
                <Row label="Enabled">
                  <YesNo value={status.reconciliation_enabled} />
                </Row>
                {status.reconciliation_auth_configured != null && (
                  <Row label="Scheduler secret">
                    <YesNo value={status.reconciliation_auth_configured} yes="Configured" no="Not configured" />
                  </Row>
                )}
                {status.reconciliation_max_reviews != null && (
                  <Row label="Reviews per run">Up to {status.reconciliation_max_reviews}</Row>
                )}
                {status.reconciliation_lookback_minutes != null && (
                  <Row label="Look-back window">{formatMinutes(status.reconciliation_lookback_minutes)}</Row>
                )}
              </dl>
              <LastRun run={status.last_reconciliation ?? null} />
            </>
          )}
        </section>
      </div>

      <section className="card p-5 sm:p-6" aria-labelledby="automation-vs-bulk">
        <h2 id="automation-vs-bulk" className="section-title">
          Automatic replies vs. bulk reply
        </h2>
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <Explainer icon={Bot} title="Automatic replies">
            Continuous: runs on the server for each new review Google reports, using the settings above.
          </Explainer>
          <Explainer icon={Layers} title="Bulk reply">
            One-time: you start it from the{' '}
            <Link to="/reviews" className="font-semibold text-brand-700 hover:text-brand-800">
              Review Inbox
            </Link>{' '}
            for the reviews pending right now, after confirming.
          </Explainer>
        </div>
        <p className="mt-4 flex items-start gap-2 text-[13px] text-ink-muted">
          <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          These settings are set on the server (environment variables) and can’t be changed from this
          page. Manual replies work the same in every mode.
        </p>
      </section>
    </div>
  )
}

function LastRun({ run }: { run: ReconciliationSummary | null }) {
  return (
    <div className="mt-5 rounded-lg border border-line bg-slate-50 p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
        <History className="h-4 w-4 text-slate-500" aria-hidden />
        Last run
      </h3>
      {!run ? (
        <p className="mt-1.5 text-[13px] text-ink-muted">No run since the server last started.</p>
      ) : (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-slate-600">
            <Badge tone={run.status === 'COMPLETED' ? 'success' : run.status === 'FAILED' ? 'danger' : 'brand'}>
              {run.status === 'COMPLETED' ? 'Completed' : run.status === 'FAILED' ? 'Failed' : 'Running'}
            </Badge>
            {run.dry_run && <Badge tone="info">Dry run</Badge>}
            <span>Started {formatTimeWithRelative(run.started_at)}</span>
          </div>
          <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {(
              [
                [run.dry_run ? 'Would publish' : 'Published', run.dry_run ? run.would_publish : run.published],
                ['Skipped', run.skipped],
                ['Failed', run.failed],
                ['Deferred', run.deferred],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className="rounded-md bg-white px-3 py-2 ring-1 ring-inset ring-line">
                <dt className="text-xs text-ink-muted">{label}</dt>
                <dd className="text-base font-semibold tabular-nums text-ink">{value}</dd>
              </div>
            ))}
          </dl>
          {run.status === 'FAILED' && (
            <p className="mt-2 text-[13px] text-red-700">The run stopped unexpectedly. It is retried on the next schedule.</p>
          )}
        </>
      )}
    </div>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="font-medium text-ink sm:text-right">{children}</dd>
    </div>
  )
}

function YesNo({ value, yes = 'Yes', no = 'No' }: { value: boolean; yes?: string; no?: string }) {
  return value ? (
    <span className="inline-flex items-center gap-1.5 text-green-700">
      <CheckCircle2 className="h-4 w-4" aria-hidden />
      {yes}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-slate-600">
      <XCircle className="h-4 w-4 text-slate-400" aria-hidden />
      {no}
    </span>
  )
}

function Explainer({ icon: Icon, title, children }: { icon: typeof Info; title: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 rounded-lg border border-line p-4">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600" aria-hidden>
        <Icon className="h-[18px] w-[18px]" />
      </span>
      <div>
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        <p className="mt-0.5 text-[13px] leading-relaxed text-slate-600">{children}</p>
      </div>
    </div>
  )
}
