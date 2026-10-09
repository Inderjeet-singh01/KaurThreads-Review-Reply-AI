import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { classNames } from '../lib/utils'

export type BadgeTone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'info'

const TONES: Record<BadgeTone, string> = {
  neutral: 'bg-slate-100 text-slate-700 ring-slate-200',
  brand: 'bg-brand-50 text-brand-700 ring-brand-100',
  success: 'bg-green-50 text-green-700 ring-green-100',
  warning: 'bg-amber-50 text-amber-800 ring-amber-100',
  danger: 'bg-red-50 text-red-700 ring-red-100',
  info: 'bg-sky-50 text-sky-800 ring-sky-100',
}

/** Small status pill. Pair color with a word (and optionally an icon), never color alone. */
export function Badge({
  tone = 'neutral',
  icon: Icon,
  dot,
  children,
  className,
}: {
  tone?: BadgeTone
  icon?: LucideIcon
  /** Leading status dot in the tone's color. */
  dot?: boolean
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={classNames(
        'inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset',
        TONES[tone],
        className,
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />}
      {Icon && <Icon className="h-3 w-3" aria-hidden />}
      {children}
    </span>
  )
}

type ReviewStatus = 'needs-reply' | 'replied'

/** Reply status of a review. */
export function StatusBadge({ status }: { status: ReviewStatus }) {
  return status === 'replied' ? (
    <Badge tone="success">Replied</Badge>
  ) : (
    <Badge tone="warning">Needs reply</Badge>
  )
}
