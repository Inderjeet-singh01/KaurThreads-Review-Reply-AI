import type { LucideIcon } from 'lucide-react'
import { classNames } from '../lib/utils'

type Accent = 'slate' | 'rose' | 'emerald' | 'amber'

interface StatsCardProps {
  label: string
  value: string | number
  accent?: Accent
  icon?: LucideIcon
  suffix?: React.ReactNode
}

const VALUE_COLOR: Record<Accent, string> = {
  slate: 'text-slate-900',
  rose: 'text-rose-600',
  emerald: 'text-emerald-600',
  amber: 'text-amber-500',
}

const ICON_BG: Record<Accent, string> = {
  slate: 'bg-slate-100 text-slate-500',
  rose: 'bg-rose-50 text-rose-500',
  emerald: 'bg-emerald-50 text-emerald-500',
  amber: 'bg-amber-50 text-amber-500',
}

export function StatsCard({
  label,
  value,
  accent = 'slate',
  icon: Icon,
  suffix,
}: StatsCardProps) {
  return (
    <div className="card p-5 transition-shadow hover:shadow-card-hover">
      <div className="flex items-start justify-between">
        <p className="text-sm font-medium text-slate-500">{label}</p>
        {Icon && (
          <span
            className={classNames(
              'flex h-8 w-8 items-center justify-center rounded-lg',
              ICON_BG[accent],
            )}
          >
            <Icon className="h-4 w-4" />
          </span>
        )}
      </div>
      <div className="mt-3 flex items-end gap-1.5">
        <span className={classNames('text-3xl font-bold tracking-tight', VALUE_COLOR[accent])}>
          {value}
        </span>
        {suffix}
      </div>
    </div>
  )
}
