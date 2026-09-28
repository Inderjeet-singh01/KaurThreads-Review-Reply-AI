import { classNames } from '../lib/utils'

type Status = 'needs-reply' | 'replied' | 'active'

const STYLES: Record<Status, string> = {
  'needs-reply': 'bg-rose-50 text-rose-600 ring-1 ring-inset ring-rose-100',
  replied: 'bg-emerald-50 text-emerald-600 ring-1 ring-inset ring-emerald-100',
  active: 'bg-emerald-50 text-emerald-600 ring-1 ring-inset ring-emerald-100',
}

const LABELS: Record<Status, string> = {
  'needs-reply': 'Needs Reply',
  replied: 'Replied',
  active: 'Active',
}

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span
      className={classNames(
        'inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold',
        STYLES[status],
      )}
    >
      {LABELS[status]}
    </span>
  )
}
