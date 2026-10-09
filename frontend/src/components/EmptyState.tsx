import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { classNames } from '../lib/utils'

interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description?: ReactNode
  action?: ReactNode
  /** Tighter padding, no dashed frame (inside cards and panes). */
  compact?: boolean
}

export function EmptyState({ icon: Icon, title, description, action, compact }: EmptyStateProps) {
  return (
    <div
      className={classNames(
        'flex flex-col items-center justify-center text-center',
        compact ? 'px-6 py-10' : 'rounded-xl border border-dashed border-line bg-white px-6 py-14',
      )}
    >
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-brand-50">
        <Icon className="h-6 w-6 text-brand-600" aria-hidden />
      </div>
      <h3 className="mt-4 text-[15px] font-semibold text-ink">{title}</h3>
      {description && <p className="mt-1.5 max-w-sm text-sm text-ink-muted">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
