import { useEffect, useState } from 'react'
import { RefreshCw, WifiOff } from 'lucide-react'
import { BrandMark } from './BrandMark'

/** After this long, explain that a sleeping free-plan backend is waking up. */
export const SLOW_START_HINT_MS = 4000

interface StartupScreenProps {
  /** Set when the connection check failed; shows a retry button. */
  error?: string
  onRetry?: () => void
}

/**
 * Branded screen shown while the app checks the Google connection. Mirrors
 * the static markup in index.html (painted before the JavaScript loads), so
 * the hand-over is seamless. Shows no account or review data.
 */
export function StartupScreen({ error, onRetry }: StartupScreenProps) {
  const [slow, setSlow] = useState(false)

  useEffect(() => {
    if (error) return
    const timer = window.setTimeout(() => setSlow(true), SLOW_START_HINT_MS)
    return () => window.clearTimeout(timer)
  }, [error])

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-sm text-center">
        <BrandMark className="mx-auto h-14 w-14 rounded-2xl shadow-card" />
        <p className="mt-4 text-lg font-bold text-ink">Review Reply AI</p>

        {error ? (
          <div role="alert" className="mt-6">
            <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-amber-100">
              <WifiOff className="h-5 w-5 text-amber-600" />
            </div>
            <p className="mt-3 text-sm font-semibold text-ink">
              We couldn’t check your connection
            </p>
            <p className="mt-1 text-sm text-slate-500">{error}</p>
            {onRetry && (
              <button className="btn-primary mt-5" onClick={onRetry}>
                <RefreshCw className="h-4 w-4" />
                Try again
              </button>
            )}
          </div>
        ) : (
          <div role="status" aria-live="polite" className="mt-6">
            <div className="mx-auto h-1 w-40 overflow-hidden rounded-full bg-brand-100">
              <div className="h-full w-1/3 animate-indeterminate rounded-full bg-brand-600" />
            </div>
            <p className="mt-3 text-sm font-medium text-slate-500">
              Checking your Google connection…
            </p>
            {slow && (
              <p className="mt-2 text-xs text-slate-400">
                Waking up the server — after a period of inactivity this can take up to a
                minute.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
