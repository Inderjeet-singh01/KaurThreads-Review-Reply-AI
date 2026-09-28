import { AlertTriangle, RefreshCw } from 'lucide-react'

interface ErrorStateProps {
  title?: string
  message: string
  onRetry?: () => void
  retrying?: boolean
}

export function ErrorState({
  title = 'Something went wrong',
  message,
  onRetry,
  retrying,
}: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-rose-100 bg-rose-50/40 px-6 py-14 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-rose-100">
        <AlertTriangle className="h-7 w-7 text-rose-500" />
      </div>
      <h3 className="mt-4 text-base font-semibold text-slate-800">{title}</h3>
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
