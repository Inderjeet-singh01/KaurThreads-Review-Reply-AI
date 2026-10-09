import { useState, type KeyboardEvent } from 'react'
import { Table2 } from 'lucide-react'
import type { BucketUnit, TrendBucket } from '../lib/reviewStats'
import { classNames } from '../lib/utils'

const UNIT_LABEL: Record<BucketUnit, string> = {
  day: 'day',
  week: 'week',
  month: 'month',
  year: 'year',
}

const plural = (n: number) => `${n} ${n === 1 ? 'review' : 'reviews'}`

function describe(bucket: TrendBucket): string {
  const rating = bucket.averageRating != null ? `, average ${bucket.averageRating.toFixed(1)}★` : ''
  return `${bucket.fullLabel}: ${plural(bucket.count)}${rating}`
}

/**
 * Reviews received per period — one series, one hue. Columns (≤24px, rounded
 * data end) grow from a single baseline; hovering or arrow keys show the exact
 * values, and a table view lists every period.
 */
export function TrendChart({ buckets, unit }: { buckets: TrendBucket[]; unit: BucketUnit }) {
  const [active, setActive] = useState<number | null>(null)
  const [showTable, setShowTable] = useState(false)

  const max = Math.max(1, ...buckets.map((b) => b.count))
  // Whole-number ticks that fit the data: at most 4 steps above zero.
  const step = Math.ceil(max / 4)
  const top = step * Math.ceil(max / step)
  const ticks = Array.from({ length: top / step + 1 }, (_, i) => i * step)
  // Label the single highest column (skipped when several tie).
  const peaks = buckets.filter((b) => b.count === max).length
  const peak = peaks === 1 ? buckets.findIndex((b) => b.count === max) : -1
  const labelEvery = Math.max(1, Math.ceil(buckets.length / 7))

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (buckets.length === 0) return
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault()
      const delta = e.key === 'ArrowRight' ? 1 : -1
      setActive((i) =>
        i == null ? (delta > 0 ? 0 : buckets.length - 1) : Math.min(buckets.length - 1, Math.max(0, i + delta)),
      )
    } else if (e.key === 'Escape') {
      setActive(null)
    }
  }

  return (
    <div>
      <div className="flex gap-2">
        {/* Y axis */}
        <div className="relative h-52 w-7 shrink-0 text-right text-[11px] tabular-nums text-ink-muted" aria-hidden>
          {ticks.map((t) => (
            <span
              key={t}
              className="absolute right-0 translate-y-1/2"
              style={{ bottom: `${(t / top) * 100}%` }}
            >
              {t}
            </span>
          ))}
        </div>

        {/* Plot */}
        <div
          className="focus-ring relative h-52 flex-1 rounded-sm"
          tabIndex={0}
          role="group"
          aria-label={`Reviews received per ${UNIT_LABEL[unit]}. Use the left and right arrow keys to read each ${UNIT_LABEL[unit]}.`}
          onKeyDown={onKeyDown}
          onMouseLeave={() => setActive(null)}
          onBlur={() => setActive(null)}
        >
          {ticks.map((t) => (
            <div
              key={t}
              className={classNames('absolute inset-x-0 border-t', t === 0 ? 'border-slate-300' : 'border-slate-100')}
              style={{ bottom: `${(t / top) * 100}%` }}
              aria-hidden
            />
          ))}
          <div className="absolute inset-0 flex items-end gap-[2px]">
            {buckets.map((bucket, i) => {
              const height = (bucket.count / top) * 100
              const isActive = active === i
              const align = i < 2 ? 'left-0' : i > buckets.length - 3 ? 'right-0' : 'left-1/2 -translate-x-1/2'
              return (
                <div
                  key={bucket.start}
                  className="relative flex h-full flex-1 items-end justify-center"
                  onMouseEnter={() => setActive(i)}
                  aria-hidden
                >
                  <div
                    className={classNames(
                      'w-full max-w-[24px] rounded-t-[4px] transition-colors',
                      isActive ? 'bg-brand-700' : 'bg-brand-500',
                    )}
                    style={{ height: bucket.count ? `max(${height}%, 3px)` : 0 }}
                  />
                  {i === peak && active == null && (
                    <span
                      className="absolute text-[11px] font-semibold tabular-nums text-ink"
                      style={{ bottom: `calc(${height}% + 4px)` }}
                    >
                      {bucket.count}
                    </span>
                  )}
                  {isActive && (
                    <div
                      className={classNames(
                        'pointer-events-none absolute z-10 w-max max-w-[12rem] rounded-lg bg-navy-900 px-2.5 py-1.5 text-xs text-white shadow-overlay',
                        align,
                      )}
                      style={{ bottom: `calc(${height}% + 8px)` }}
                    >
                      <p className="font-semibold">{bucket.fullLabel}</p>
                      <p className="text-slate-300">
                        {plural(bucket.count)}
                        {bucket.averageRating != null && ` · ${bucket.averageRating.toFixed(1)}★ avg`}
                      </p>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
          <p className="sr-only" aria-live="polite">
            {active != null && buckets[active] ? describe(buckets[active]) : ''}
          </p>
        </div>
      </div>

      {/* X axis */}
      <div className="ml-9 mt-2 flex gap-[2px]" aria-hidden>
        {buckets.map((bucket, i) => (
          <span
            key={bucket.start}
            className="flex-1 overflow-visible whitespace-nowrap text-center text-[11px] text-ink-muted"
          >
            {i % labelEvery === 0 ? bucket.label : ''}
          </span>
        ))}
      </div>

      <button
        type="button"
        onClick={() => setShowTable((v) => !v)}
        aria-expanded={showTable}
        className="focus-ring mt-4 inline-flex items-center gap-1.5 rounded-md text-[13px] font-medium text-brand-700 hover:text-brand-800"
      >
        <Table2 className="h-3.5 w-3.5" aria-hidden />
        {showTable ? 'Hide table' : 'View as table'}
      </button>
      {showTable && (
        <div className="scroll-area mt-3 max-h-64 overflow-auto rounded-lg border border-line">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-slate-50 text-xs uppercase tracking-wide text-ink-muted">
              <tr>
                <th scope="col" className="px-3 py-2 font-semibold">Period</th>
                <th scope="col" className="px-3 py-2 text-right font-semibold">Reviews</th>
                <th scope="col" className="px-3 py-2 text-right font-semibold">Avg rating</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {buckets.map((b) => (
                <tr key={b.start}>
                  <td className="px-3 py-2 text-slate-700">{b.fullLabel}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-ink">{b.count}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-ink">
                    {b.averageRating != null ? b.averageRating.toFixed(1) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
