import { AlertTriangle, RefreshCw } from 'lucide-react'
import { classNames } from '../lib/utils'

interface ErrorStateProps {
  title?: string
  message: string
  onRetry?: () => void
  retrying?: boolean
  compact?: boolean
}

export function ErrorState({
  title = 'Something went wrong',
  message,
  onRetry,
  retrying,
  compact,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={classNames(
        'flex flex-col items-center justify-center text-center',
        compact ? 'px-6 py-10' : 'rounded-xl border border-red-100 bg-red-50/40 px-6 py-12',
      )}
    >
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red-100">
        <AlertTriangle className="h-6 w-6 text-red-600" aria-hidden />
      </div>
      <h3 className="mt-4 text-[15px] font-semibold text-ink">{title}</h3>
      <p className="mt-1.5 max-w-md text-sm text-slate-600">{message}</p>
      {onRetry && (
        <button className="btn-secondary mt-5" onClick={onRetry} disabled={retrying}>
          <RefreshCw className={retrying ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />
          {retrying ? 'Retrying…' : 'Try again'}
        </button>
      )}
    </div>
  )
}
