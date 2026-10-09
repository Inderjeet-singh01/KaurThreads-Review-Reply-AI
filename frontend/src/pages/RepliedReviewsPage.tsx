import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronUp, ExternalLink, MessageSquareReply, SearchX } from 'lucide-react'
import { useBusiness } from '../context/BusinessContext'
import { useReviews } from '../context/ReviewsContext'
import type { Review } from '../lib/types'
import {
  classNames,
  formatFullDate,
  formatRelativeDate,
  formatTimeWithRelative,
  googleMapsUrl,
  timestamp,
} from '../lib/utils'
import { useSignOutOnAuthError } from '../lib/useAuthExpiry'
import { PageHeader } from '../components/PageHeader'
import { Avatar } from '../components/Avatar'
import { RatingStars } from '../components/RatingStars'
import { ReviewListSkeleton } from '../components/Skeletons'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { SearchInput, SelectField } from '../components/FormControls'

type Sort = 'recent-reply' | 'oldest-reply' | 'newest' | 'oldest' | 'highest' | 'lowest'
type RatingFilter = 'all' | '5' | '4' | '3' | '2' | '1'

const SORT_OPTIONS: ReadonlyArray<{ value: Sort; label: string }> = [
  { value: 'recent-reply', label: 'Recently replied' },
  { value: 'oldest-reply', label: 'Oldest reply' },
  { value: 'newest', label: 'Newest review' },
  { value: 'oldest', label: 'Oldest review' },
  { value: 'highest', label: 'Highest rating' },
  { value: 'lowest', label: 'Lowest rating' },
]

const RATING_OPTIONS: ReadonlyArray<{ value: RatingFilter; label: string }> = [
  { value: 'all', label: 'All ratings' },
  { value: '5', label: '5 stars' },
  { value: '4', label: '4 stars' },
  { value: '3', label: '3 stars' },
  { value: '2', label: '2 stars' },
  { value: '1', label: '1 star' },
]

export function RepliedReviewsPage({ onAuthExpired }: { onAuthExpired: () => void }) {
  const { business } = useBusiness()
  const { replied, loading, error, isAuthError, refresh } = useReviews()
  useSignOutOnAuthError(isAuthError, onAuthExpired)

  const [query, setQuery] = useState('')
  const [rating, setRating] = useState<RatingFilter>('all')
  const [sort, setSort] = useState<Sort>('recent-reply')

  const googleUrl = business ? googleMapsUrl(business.name, business.address) : undefined

  const items = useMemo(() => {
    const q = query.trim().toLowerCase()
    const filtered = replied.filter(
      (r) =>
        (rating === 'all' || r.rating === Number(rating)) &&
        (!q ||
          r.reviewer.toLowerCase().includes(q) ||
          r.review.toLowerCase().includes(q) ||
          (r.reply_comment ?? '').toLowerCase().includes(q)),
    )
    return [...filtered].sort((a, b) => {
      switch (sort) {
        case 'recent-reply':
          return timestamp(b.reply_updated_at) - timestamp(a.reply_updated_at)
        case 'oldest-reply':
          return timestamp(a.reply_updated_at) - timestamp(b.reply_updated_at)
        case 'oldest':
          return timestamp(a.created_at) - timestamp(b.created_at)
        case 'highest':
          return (b.rating ?? 0) - (a.rating ?? 0)
        case 'lowest':
          return (a.rating ?? 0) - (b.rating ?? 0)
        default:
          return timestamp(b.created_at) - timestamp(a.created_at)
      }
    })
  }, [replied, query, rating, sort])

  const filtersActive = query.trim() !== '' || rating !== 'all'

  return (
    <div className="space-y-5">
      <PageHeader
        title="Replied Reviews"
        description={
          loading
            ? 'Reviews you’ve already answered on Google.'
            : `${replied.length} ${replied.length === 1 ? 'review has' : 'reviews have'} a reply on Google.`
        }
      />

      <div className="card overflow-hidden">
        <div className="flex flex-col gap-2 border-b border-line p-4 sm:flex-row sm:items-center">
          <SearchInput
            className="sm:max-w-xs sm:flex-1"
            label="Search replied reviews"
            placeholder="Search reviewer, review or reply…"
            value={query}
            onChange={setQuery}
          />
          <div className="grid grid-cols-2 gap-2 sm:ml-auto sm:flex">
            <SelectField label="Filter by rating" value={rating} onChange={setRating} options={RATING_OPTIONS} />
            <SelectField label="Sort replied reviews" value={sort} onChange={setSort} options={SORT_OPTIONS} />
          </div>
        </div>

        {loading ? (
          <ReviewListSkeleton count={4} />
        ) : error ? (
          <ErrorState compact message={error} onRetry={() => void refresh()} />
        ) : items.length === 0 ? (
          filtersActive ? (
            <EmptyState
              compact
              icon={SearchX}
              title="No matching replies"
              description="Try a different search term or rating."
              action={
                <button
                  className="btn-secondary btn-sm"
                  onClick={() => {
                    setQuery('')
                    setRating('all')
                  }}
                >
                  Clear filters
                </button>
              }
            />
          ) : (
            <EmptyState
              compact
              icon={MessageSquareReply}
              title="No replied reviews yet"
              description="Once you reply to reviews, they’ll appear here."
            />
          )
        ) : (
          <ul className="divide-y divide-line">
            {items.map((review) => (
              <RepliedItem key={review.review_id} review={review} googleUrl={googleUrl} />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function RepliedItem({ review, googleUrl }: { review: Review; googleUrl?: string }) {
  const [expanded, setExpanded] = useState(false)
  const reply = review.reply_comment || 'Reply posted to Google.'
  const long = reply.length > 180 || reply.includes('\n')

  return (
    <li className="px-4 py-4 sm:px-5">
      <div className="flex items-start gap-3">
        <Avatar name={review.reviewer} photoUrl={review.profile_photo_url} size="sm" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <Link
              to={`/reviews/${encodeURIComponent(review.review_id)}`}
              className="focus-ring rounded text-sm font-semibold text-ink hover:text-brand-700"
            >
              {review.reviewer}
            </Link>
            <time
              dateTime={review.created_at ?? undefined}
              title={formatFullDate(review.created_at)}
              className="text-xs text-ink-muted"
            >
              {formatRelativeDate(review.created_at)}
            </time>
          </div>
          <div className="mt-1">
            <RatingStars rating={review.rating} size="sm" />
          </div>
          <p
            className={classNames(
              'mt-2 text-sm leading-relaxed',
              review.review ? 'text-slate-700' : 'italic text-slate-400',
            )}
          >
            {review.review || 'Rating only — no written comment'}
          </p>

          <div className="mt-3 rounded-lg border-l-[3px] border-brand-300 bg-slate-50 px-3.5 py-2.5">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
              <p className="text-xs font-semibold text-slate-700">Your reply</p>
              {review.reply_updated_at && (
                <time dateTime={review.reply_updated_at} className="text-xs text-ink-muted">
                  {formatTimeWithRelative(review.reply_updated_at)}
                </time>
              )}
            </div>
            <p
              className={classNames(
                'mt-1 whitespace-pre-wrap text-sm leading-relaxed text-slate-600',
                !expanded && long && 'line-clamp-2',
              )}
            >
              {reply}
            </p>
            {long && (
              <button
                onClick={() => setExpanded((v) => !v)}
                aria-expanded={expanded}
                className="focus-ring mt-1 inline-flex items-center gap-1 rounded text-xs font-semibold text-brand-700 hover:text-brand-800"
              >
                {expanded ? (
                  <ChevronUp className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                )}
                {expanded ? 'Show less' : 'Show full reply'}
              </button>
            )}
          </div>

          {googleUrl && (
            <a
              href={googleUrl}
              target="_blank"
              rel="noreferrer"
              className="focus-ring mt-2.5 inline-flex items-center gap-1.5 rounded text-xs font-medium text-ink-muted hover:text-brand-700"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              View business on Google Maps
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          )}
        </div>
      </div>
    </li>
  )
}
