import { Star } from 'lucide-react'
import type { RatingBucket } from '../lib/reviewStats'

/**
 * Star-rating distribution as horizontal meters (single series: the share of
 * rated reviews per star). Every value is also printed as text, so the bars
 * never carry information on their own.
 */
export function RatingDistribution({ buckets }: { buckets: RatingBucket[] }) {
  return (
    <ul className="space-y-3.5" aria-label="Rating distribution">
      {buckets.map(({ stars, count, percent }) => (
        <li key={stars} className="flex items-center gap-3 text-sm">
          <span className="flex w-12 shrink-0 items-center gap-1 font-medium text-slate-600">
            {stars}
            <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" aria-hidden />
            <span className="sr-only">{stars === 1 ? 'star' : 'stars'}</span>
          </span>
          <span className="h-2 flex-1 overflow-hidden rounded-full bg-brand-50" aria-hidden>
            <span
              className="block h-full rounded-full bg-brand-500 transition-[width] duration-300"
              style={{ width: `${percent}%` }}
            />
          </span>
          <span className="w-[5.5rem] shrink-0 text-right tabular-nums text-ink-muted">
            <span className="font-semibold text-ink">{percent}%</span> ({count})
          </span>
        </li>
      ))}
    </ul>
  )
}
