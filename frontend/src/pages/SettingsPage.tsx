import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Bot,
  Building2,
  CheckCircle2,
  ChevronRight,
  Link2,
  LogOut,
  MessageSquareText,
  RefreshCw,
  Repeat,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { ApiError, api } from '../lib/api'
import type { AuthStatus, AutomationStatus, ReplyLength, ReplyTone } from '../lib/types'
import { useBusiness } from '../context/BusinessContext'
import { useReviews } from '../context/ReviewsContext'
import { useToast } from '../context/ToastContext'
import { readReplyPreferences, saveReplyPreferences } from '../lib/replyPreferences'
import { classNames, formatDateTime } from '../lib/utils'
import { PageHeader } from '../components/PageHeader'
import { Avatar } from '../components/Avatar'
import { Badge } from '../components/StatusBadge'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ReplyControls } from '../components/ReplyControls'
import { resetBusinessSwitcherCache } from '../components/BusinessSwitcher'
import { clearReplyDrafts } from '../components/ReplyWorkspace'

const SECTIONS: Array<{ id: string; label: string; icon: LucideIcon }> = [
  { id: 'google', label: 'Google connection', icon: Link2 },
  { id: 'business', label: 'Business', icon: Building2 },
  { id: 'replies', label: 'Reply preferences', icon: MessageSquareText },
  { id: 'automation', label: 'Automation', icon: Bot },
  { id: 'account', label: 'Account', icon: LogOut },
]

export function SettingsPage({ onAuthExpired }: { onAuthExpired: () => void }) {
  const { business, clearBusiness } = useBusiness()
  // Live counts; the stored business only has a snapshot from when it was picked.
  const { stats, loading: reviewsLoading, error: reviewsError } = useReviews()
  const toast = useToast()
  const navigate = useNavigate()

  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [statusLoading, setStatusLoading] = useState(true)
  const [automation, setAutomation] = useState<AutomationStatus | null>(null)
  const [automationFailed, setAutomationFailed] = useState(false)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  const [preferences, setPreferences] = useState(readReplyPreferences)

  const loadStatus = useCallback(async () => {
    setStatusLoading(true)
    try {
      setStatus(await api.getAuthStatus())
    } catch {
      setStatus({ authenticated: false, retryable: true, reason: 'Could not read the connection status.' })
    } finally {
      setStatusLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadStatus()
    api
      .getAutomationStatus()
      .then(setAutomation)
      .catch(() => setAutomationFailed(true))
  }, [loadStatus])

  const updatePreferences = (next: { tone?: ReplyTone; length?: ReplyLength }) => {
    const merged = { ...preferences, ...next }
    setPreferences(merged)
    saveReplyPreferences(merged)
    toast.success('Default reply style saved.')
  }

  const disconnect = async () => {
    setDisconnecting(true)
    try {
      await api.logout()
      clearBusiness()
      resetBusinessSwitcherCache()
      clearReplyDrafts()
      setConfirmDisconnect(false)
      onAuthExpired()
      toast.success('Google account disconnected.')
      navigate('/login')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not disconnect. Please try again.')
    } finally {
      setDisconnecting(false)
    }
  }

  const connected = status?.authenticated

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Manage your Google connection, business and reply preferences." />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="hidden lg:block">
          <ul className="sticky top-6 space-y-1">
            {SECTIONS.map(({ id, label, icon: Icon }) => (
              <li key={id}>
                <a
                  href={`#${id}`}
                  className="focus-ring flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-600 hover:bg-white hover:text-ink"
                >
                  <Icon className="h-4 w-4 text-slate-400" aria-hidden />
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="max-w-3xl space-y-6">
          <Section id="google" title="Google connection" description="The Google account that manages your Business Profile.">
            <div className="flex flex-col gap-4 rounded-lg border border-line bg-slate-50 p-4 sm:flex-row sm:items-center">
              {statusLoading ? (
                <div className="h-10 w-56 animate-pulse rounded bg-slate-200" aria-label="Checking connection" />
              ) : connected ? (
                <>
                  <CheckCircle2 className="h-6 w-6 shrink-0 text-green-600" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                      Connected to Google Business Profile <Badge tone="success">Connected</Badge>
                    </p>
                    {status?.expires_at && (
                      <p className="mt-0.5 text-[13px] text-ink-muted">
                        Access token renews automatically · current one valid until {formatDateTime(status.expires_at)}
                      </p>
                    )}
                  </div>
                </>
              ) : (
                <>
                  <XCircle className="h-6 w-6 shrink-0 text-red-600" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-ink">
                      {status?.retryable ? 'Connection could not be checked' : 'Not connected'}
                    </p>
                    <p className="mt-0.5 text-[13px] text-ink-muted">
                      {status?.retryable
                        ? 'The server is temporarily unavailable. Your connection has not changed.'
                        : 'Reconnect your Google account to continue.'}
                    </p>
                  </div>
                </>
              )}
              <button className="btn-secondary btn-sm shrink-0" onClick={() => void loadStatus()} disabled={statusLoading}>
                <RefreshCw className={classNames('h-3.5 w-3.5', statusLoading && 'animate-spin')} aria-hidden />
                Check again
              </button>
            </div>
            {!statusLoading && !connected && !status?.retryable && (
              <button className="btn-primary mt-4" onClick={() => navigate('/login')}>
                Reconnect Google
              </button>
            )}
            <p className="mt-3 text-[13px] text-ink-muted">
              Google credentials are stored encrypted on the server and are never sent to this browser.
            </p>
          </Section>

          <Section id="business" title="Business" description="The Business Profile location you are managing.">
            {business ? (
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
                <Avatar name={business.name} size="lg" square />
                <dl className="min-w-0 flex-1 text-sm">
                  <dt className="sr-only">Name</dt>
                  <dd className="truncate font-semibold text-ink">{business.name}</dd>
                  {business.address && (
                    <>
                      <dt className="sr-only">Address</dt>
                      <dd className="truncate text-ink-muted">{business.address}</dd>
                    </>
                  )}
                  <dt className="sr-only">Location ID</dt>
                  <dd className="truncate font-mono text-xs text-ink-muted">ID {business.location_id}</dd>
                  {!reviewsLoading && !reviewsError && (
                    <>
                      <dt className="sr-only">Reviews</dt>
                      <dd className="mt-1 text-[13px] text-slate-600">
                        {stats.total_reviews} reviews · {stats.unanswered} need a reply
                      </dd>
                    </>
                  )}
                </dl>
                <button className="btn-secondary shrink-0" onClick={() => navigate('/select-business')}>
                  <Repeat className="h-4 w-4" aria-hidden />
                  Change business
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-3 text-sm text-ink-muted">
                <Building2 className="h-5 w-5 text-slate-400" aria-hidden />
                No business selected.
                <button className="font-semibold text-brand-700 hover:text-brand-800" onClick={() => navigate('/select-business')}>
                  Select one
                </button>
              </div>
            )}
          </Section>

          <Section
            id="replies"
            title="Reply preferences"
            description="Default style for new AI drafts. You can still change it for each review. Saved in this browser."
          >
            <ReplyControls
              tone={preferences.tone}
              length={preferences.length}
              onToneChange={(tone) => updatePreferences({ tone })}
              onLengthChange={(length) => updatePreferences({ length })}
            />
          </Section>

          <Section id="automation" title="Automation" description="Automatic replies to new reviews are configured on the server.">
            <Link
              to="/automation"
              className="focus-ring flex items-center gap-3 rounded-lg border border-line p-4 transition-colors hover:border-brand-200 hover:bg-brand-50/40"
            >
              <Bot className="h-5 w-5 shrink-0 text-brand-600" aria-hidden />
              <span className="min-w-0 flex-1 text-sm">
                <span className="block font-semibold text-ink">
                  {automation
                    ? !automation.enabled
                      ? 'Off — new reviews are only answered manually'
                      : automation.dry_run
                        ? 'Dry run — replies are generated and checked but not posted'
                        : 'On — validated replies to new reviews are posted automatically'
                    : automationFailed
                      ? 'Status unavailable right now'
                      : 'Loading status…'}
                </span>
                <span className="block text-[13px] text-ink-muted">View the full automation status</span>
              </span>
              <ChevronRight className="h-4 w-4 text-slate-400" aria-hidden />
            </Link>
          </Section>

          <Section id="account" title="Account" description="Disconnect this app from your Google account.">
            <div className="flex flex-col gap-3 rounded-lg border border-red-100 bg-red-50/40 p-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-[13px] text-slate-700">
                Deletes the stored Google credentials. Replies already published stay on Google.
              </p>
              <button className="btn-danger shrink-0" onClick={() => setConfirmDisconnect(true)} disabled={!connected}>
                <LogOut className="h-4 w-4" aria-hidden />
                Disconnect Google
              </button>
            </div>
          </Section>
        </div>
      </div>

      <ConfirmDialog
        open={confirmDisconnect}
        tone="danger"
        title="Disconnect your Google account?"
        description="The app will stop managing reviews until you sign in with Google again. Automatic replies also stop working, because they use the same connection."
        confirmLabel="Disconnect"
        busyLabel="Disconnecting…"
        busy={disconnecting}
        onConfirm={() => void disconnect()}
        onCancel={() => setConfirmDisconnect(false)}
      />
    </div>
  )
}

function Section({
  id,
  title,
  description,
  children,
}: {
  id: string
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <section id={id} className="card scroll-mt-6 p-5 sm:p-6" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="section-title">
        {title}
      </h2>
      {description && <p className="mt-0.5 text-[13px] text-ink-muted">{description}</p>}
      <div className="mt-4">{children}</div>
    </section>
  )
}
