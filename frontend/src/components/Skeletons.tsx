// Skeleton loaders that hold the final layout while data is fetching.

const bar = 'animate-pulse rounded bg-slate-200'

export function ReviewRowSkeleton() {
  return (
    <div className="flex items-start gap-3 border-b border-line px-4 py-3.5 last:border-0" aria-hidden>
      <div className="h-9 w-9 shrink-0 animate-pulse rounded-full bg-slate-200" />
      <div className="flex-1 space-y-2">
        <div className="flex items-center justify-between">
          <div className={`${bar} h-3.5 w-28`} />
          <div className={`${bar} h-3 w-12`} />
        </div>
        <div className={`${bar} h-3 w-20`} />
        <div className={`${bar} h-3 w-full`} />
      </div>
    </div>
  )
}

export function ReviewListSkeleton({ count = 5 }: { count?: number }) {
  return (
    <div role="status" aria-label="Loading reviews">
      {Array.from({ length: count }).map((_, i) => (
        <ReviewRowSkeleton key={i} />
      ))}
    </div>
  )
}

export function PanelSkeleton({ lines = 4 }: { lines?: number }) {
  return (
    <div className="space-y-3 p-5" aria-hidden>
      <div className={`${bar} h-4 w-40`} />
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className={`${bar} h-3`} style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  )
}

export function BusinessCardSkeleton() {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-line p-4" aria-hidden>
      <div className="h-11 w-11 animate-pulse rounded-xl bg-slate-200" />
      <div className="flex-1 space-y-2">
        <div className={`${bar} h-4 w-40`} />
        <div className={`${bar} h-3 w-56`} />
      </div>
    </div>
  )
}
