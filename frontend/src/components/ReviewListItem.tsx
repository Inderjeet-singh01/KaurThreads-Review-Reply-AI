import { Link } from 'react-router-dom'
import type { Review } from '../lib/types'
import { classNames, formatFullDate, formatRelativeDate } from '../lib/utils'
import { Avatar } from './Avatar'
import { RatingStars } from './RatingStars'
import { StatusBadge } from './StatusBadge'

interface ReviewListItemProps {
  review: Review
  to: string
  selected?: boolean
  /** Hide the reply-status badge (e.g. in a list that only holds pending reviews). */
  hideStatus?: boolean
}

/** Compact, clickable review row: who, rating, when, a preview, and reply status. */
export function ReviewListItem({ review, to, selected, hideStatus }: ReviewListItemProps) {
  return (
    <Link
      to={to}
      aria-current={selected ? 'page' : undefined}
      className={classNames(
        'relative flex items-start gap-3 border-b border-line px-4 py-3.5 transition-colors last:border-b-0 focus:outline-none focus-visible:bg-brand-50/60 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500',
        selected ? 'bg-brand-50/70' : 'hover:bg-slate-50',
      )}
    >
      {selected && <span className="absolute inset-y-0 left-0 w-[3px] bg-brand-600" aria-hidden />}
      <Avatar name={review.reviewer} photoUrl={review.profile_photo_url} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <p className="truncate text-sm font-semibold text-ink">{review.reviewer}</p>
          <time
            dateTime={review.created_at ?? undefined}
            title={formatFullDate(review.created_at)}
            className="shrink-0 text-xs text-ink-muted"
          >
            {formatRelativeDate(review.created_at)}
          </time>
        </div>
        <div className="mt-1 flex items-center justify-between gap-2">
          <RatingStars rating={review.rating} size="sm" />
          {!hideStatus && <StatusBadge status={review.has_reply ? 'replied' : 'needs-reply'} />}
        </div>
        <p
          className={classNames(
            'mt-1.5 line-clamp-2 text-[13px] leading-relaxed',
            review.review ? 'text-slate-600' : 'italic text-slate-400',
          )}
        >
          {review.review || 'Rating only — no written comment'}
        </p>
      </div>
    </Link>
  )
}
