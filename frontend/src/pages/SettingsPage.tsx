import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, Building2, CheckCircle2, LogOut, RefreshCw, Repeat, XCircle } from 'lucide-react'
import { ApiError, api } from '../lib/api'
import type { AuthStatus, AutomationStatus } from '../lib/types'
import { useBusiness } from '../context/BusinessContext'
import { useToast } from '../context/ToastContext'
import { Avatar } from '../components/Avatar'
import { formatFullDate } from '../lib/utils'

export function SettingsPage({ onAuthExpired }: { onAuthExpired: () => void }) {
  const { business, clearBusiness } = useBusiness()
  const toast = useToast()
  const navigate = useNavigate()

  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [disconnecting, setDisconnecting] = useState(false)
  const [automation, setAutomation] = useState<AutomationStatus | null>(null)

  const loadStatus = async () => {
    setLoading(true)
    try {
      setStatus(await api.getAuthStatus())
    } catch {
      setStatus({ authenticated: false, reason: 'Could not read connection status.' })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadStatus()
    api.getAutomationStatus().then(setAutomation).catch(() => setAutomation(null))
  }, [])

  const handleDisconnect = async () => {
    setDisconnecting(true)
    try {
      await api.logout()
      clearBusiness()
      onAuthExpired()
      toast.success('Google account disconnected.')
      navigate('/login')
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : 'Could not disconnect. Please try again.',
      )
    } finally {
      setDisconnecting(false)
    }
  }

  const connected = status?.authenticated

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-lg font-bold text-slate-900">Settings</h1>
        <p className="text-sm text-slate-500">
          Manage your Google connection and selected business.
        </p>
      </div>

      {/* Google connection */}
      <section className="card p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold text-slate-900">Google Connection</h2>
          <button
            onClick={loadStatus}
            className="btn-ghost !px-2 !py-1.5 text-xs"
            disabled={loading}
          >
            <RefreshCw className={loading ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} />
            Refresh
          </button>
        </div>

        <div className="mt-4 flex items-center gap-3 rounded-lg border border-slate-100 bg-slate-50 p-4">
          {loading ? (
            <div className="h-6 w-40 animate-pulse rounded bg-slate-200" />
          ) : connected ? (
            <>
              <CheckCircle2 className="h-5 w-5 text-emerald-500" />
              <div>
                <p className="text-sm font-semibold text-slate-800">
                  Connected to Google Business Profile
                </p>
                {status?.expires_at && (
                  <p className="text-xs text-slate-500">
                    Session valid until {formatFullDate(status.expires_at)}
                  </p>
                )}
              </div>
            </>
          ) : (
            <>
              <XCircle className="h-5 w-5 text-rose-500" />
              <div>
                <p className="text-sm font-semibold text-slate-800">Not connected</p>
                <p className="text-xs text-slate-500">
                  {status?.reason || 'Reconnect your Google account to continue.'}
                </p>
              </div>
            </>
          )}
        </div>

        <div className="mt-4 flex flex-wrap gap-3">
          {connected ? (
            <button
              className="btn-secondary text-rose-600 hover:bg-rose-50"
              onClick={handleDisconnect}
              disabled={disconnecting}
            >
              <LogOut className="h-4 w-4" />
              {disconnecting ? 'Disconnecting…' : 'Disconnect account'}
            </button>
          ) : (
            <button className="btn-primary" onClick={() => navigate('/login')}>
              Reconnect Google
            </button>
          )}
        </div>
      </section>

      {/* Automatic replies (read-only: configured on the server) */}
      {automation && (
        <section className="card p-5">
          <h2 className="text-sm font-bold text-slate-900">Automatic Replies</h2>
          <div className="mt-4 flex items-center gap-3 rounded-lg border border-slate-100 bg-slate-50 p-4">
            <Bot
              className={
                automation.enabled && !automation.dry_run
                  ? 'h-5 w-5 text-emerald-500'
                  : 'h-5 w-5 text-slate-400'
              }
            />
            <div>
              <p className="text-sm font-semibold text-slate-800">
                {!automation.enabled
                  ? 'Off — new reviews are only answered manually'
                  : automation.dry_run
                    ? 'Dry run — replies are generated and checked but not posted'
                    : 'On — validated replies to new reviews are posted automatically'}
              </p>
              <p className="text-xs text-slate-500">
                Set on the server (AUTO_REPLY_ENABLED / AUTO_REPLY_DRY_RUN). Manual
                replies work the same either way.
              </p>
            </div>
          </div>
        </section>
      )}

      {/* Current business */}
      <section className="card p-5">
        <h2 className="text-sm font-bold text-slate-900">Current Business</h2>
        {business ? (
          <div className="mt-4 flex items-center gap-3">
            <Avatar name={business.name} size="lg" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-slate-900">{business.name}</p>
              {business.address && (
                <p className="truncate text-xs text-slate-500">{business.address}</p>
              )}
              <p className="mt-0.5 text-xs text-slate-500">
                {business.total_reviews} reviews · {business.unanswered} unanswered
              </p>
            </div>
            <button
              className="btn-secondary shrink-0"
              onClick={() => navigate('/select-business')}
            >
              <Repeat className="h-4 w-4" />
              Change
            </button>
          </div>
        ) : (
          <div className="mt-4 flex items-center gap-3 text-sm text-slate-500">
            <Building2 className="h-5 w-5 text-slate-400" />
            No business selected.
            <button
              className="font-semibold text-brand-600 hover:text-brand-700"
              onClick={() => navigate('/select-business')}
            >
              Select one
            </button>
          </div>
        )}
      </section>
    </div>
  )
}
