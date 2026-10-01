import { ExternalLink, Sparkles } from 'lucide-react'
import type { Review } from '../lib/types'
import { formatRelativeDate, formatTimeWithRelative } from '../lib/utils'
import { Avatar } from './Avatar'
import { RatingStars } from './RatingStars'
import { StatusBadge } from './StatusBadge'

interface ReviewCardProps {
  review: Review
  googleUrl?: string
  onOpen?: () => void
  onReplyWithAI?: () => void
  showReply?: boolean
}

export function ReviewCard({
  review,
  googleUrl,
  onOpen,
  onReplyWithAI,
  showReply,
}: ReviewCardProps) {
  return (
    <article
      className="group border-b border-slate-100 px-5 py-4 transition-colors last:border-0 hover:bg-slate-50/60"
      onClick={onOpen}
      role={onOpen ? 'button' : undefined}
      tabIndex={onOpen ? 0 : undefined}
      onKeyDown={(e) => {
        if (onOpen && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault()
          onOpen()
        }
      }}
      style={onOpen ? { cursor: 'pointer' } : undefined}
    >
      <div className="flex items-start gap-3">
        <Avatar name={review.reviewer} photoUrl={review.profile_photo_url} size="md" />

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <h3 className="truncate text-sm font-semibold text-slate-900">
                  {review.reviewer}
                </h3>
                <span className="text-xs text-slate-400">
                  {formatRelativeDate(review.created_at)}
                </span>
              </div>
              <div className="mt-1">
                <RatingStars rating={review.rating} size="sm" />
              </div>
            </div>
            <StatusBadge status={review.has_reply ? 'replied' : 'needs-reply'} />
          </div>

          {review.review ? (
            <p className="mt-2 text-sm leading-relaxed text-slate-600">{review.review}</p>
          ) : (
            <p className="mt-2 text-sm italic text-slate-400">
              (No written comment — rating only)
            </p>
          )}

          {showReply && review.has_reply && (
            <div className="mt-3 rounded-lg border border-slate-100 bg-slate-50 px-3.5 py-3">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                <p className="text-xs font-semibold text-slate-700">Your Reply</p>
                {review.reply_updated_at && (
                  <time
                    dateTime={review.reply_updated_at}
                    className="text-xs text-slate-400"
                  >
                    Replied {formatTimeWithRelative(review.reply_updated_at)}
                  </time>
                )}
              </div>
              <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-slate-600">
                {review.reply_comment || 'Reply posted to Google.'}
              </p>
            </div>
          )}

          <div className="mt-3 flex items-center justify-between gap-2">
            {googleUrl ? (
              <a
                href={googleUrl}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-brand-600"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                View on Google
              </a>
            ) : (
              <span />
            )}

            {onReplyWithAI && (
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onReplyWithAI()
                }}
                className="btn-primary !px-3.5 !py-2 text-xs"
              >
                <Sparkles className="h-3.5 w-3.5" />
                Reply with AI
              </button>
            )}
          </div>
        </div>
      </div>
    </article>
  )
}
