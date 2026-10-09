import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { classNames } from '../lib/utils'

type Accent = 'brand' | 'amber' | 'green' | 'sky'

const ICON_STYLES: Record<Accent, string> = {
  brand: 'bg-brand-50 text-brand-600',
  amber: 'bg-amber-50 text-amber-600',
  green: 'bg-green-50 text-green-600',
  sky: 'bg-sky-50 text-sky-600',
}

interface MetricCardProps {
  label: string
  /** Shown as a skeleton while `loading`. */
  value: ReactNode
  /** Short explanation under the value (real context only, e.g. "Based on 248 reviews"). */
  hint?: ReactNode
  icon: LucideIcon
  accent?: Accent
  loading?: boolean
  /** Rendered next to the value (e.g. rating stars). */
  adornment?: ReactNode
}

export function MetricCard({
  label,
  value,
  hint,
  icon: Icon,
  accent = 'brand',
  loading,
  adornment,
}: MetricCardProps) {
  return (
    <div className="card flex items-start gap-4 p-4 sm:p-5">
      <span
        className={classNames(
          'hidden h-11 w-11 shrink-0 items-center justify-center rounded-xl sm:flex',
          ICON_STYLES[accent],
        )}
        aria-hidden
      >
        <Icon className="h-5 w-5" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-ink-muted">{label}</p>
        {loading ? (
          <div className="mt-2 space-y-2" aria-hidden>
            <div className="h-7 w-16 animate-pulse rounded-md bg-slate-200" />
            <div className="h-3 w-28 animate-pulse rounded bg-slate-100" />
          </div>
        ) : (
          <>
            <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <span className="text-2xl font-bold leading-tight tracking-tight text-ink sm:text-[28px]">
                {value}
              </span>
              {adornment}
            </div>
            {hint && <p className="mt-0.5 text-xs text-ink-muted sm:text-[13px]">{hint}</p>}
          </>
        )}
      </div>
    </div>
  )
}
