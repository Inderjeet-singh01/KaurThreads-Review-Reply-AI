// Skeleton loaders used while data is fetching.

export function StatCardSkeleton() {
  return (
    <div className="card animate-pulse p-5">
      <div className="h-3 w-24 rounded bg-slate-200" />
      <div className="mt-4 h-8 w-16 rounded bg-slate-200" />
    </div>
  )
}

export function ReviewCardSkeleton() {
  return (
    <div className="animate-pulse border-b border-slate-100 px-5 py-4 last:border-0">
      <div className="flex items-start gap-3">
        <div className="h-10 w-10 rounded-full bg-slate-200" />
        <div className="flex-1 space-y-2.5">
          <div className="flex items-center justify-between">
            <div className="h-4 w-32 rounded bg-slate-200" />
            <div className="h-5 w-20 rounded-full bg-slate-200" />
          </div>
          <div className="h-3 w-24 rounded bg-slate-200" />
          <div className="h-3 w-full rounded bg-slate-200" />
          <div className="h-3 w-2/3 rounded bg-slate-200" />
        </div>
      </div>
    </div>
  )
}

export function ReviewListSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="card overflow-hidden">
      {Array.from({ length: count }).map((_, i) => (
        <ReviewCardSkeleton key={i} />
      ))}
    </div>
  )
}

export function BusinessCardSkeleton() {
  return (
    <div className="card animate-pulse p-4">
      <div className="flex items-center gap-3">
        <div className="h-12 w-12 rounded-lg bg-slate-200" />
        <div className="flex-1 space-y-2">
          <div className="h-4 w-40 rounded bg-slate-200" />
          <div className="h-3 w-56 rounded bg-slate-200" />
          <div className="h-3 w-32 rounded bg-slate-200" />
        </div>
      </div>
    </div>
  )
}
