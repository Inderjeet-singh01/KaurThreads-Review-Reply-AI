import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ChevronDown,
  Inbox,
  MessageSquare,
  MessagesSquare,
  Search,
  Star,
} from 'lucide-react'
import { useBusiness } from '../context/BusinessContext'
import { useReviews } from '../context/ReviewsContext'
import type { Review } from '../lib/types'
import { StatsCard } from '../components/StatsCard'
import { ReviewCard } from '../components/ReviewCard'
import { ReviewListSkeleton } from '../components/Skeletons'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { BulkReplyPanel } from '../components/BulkReplyPanel'
import { classNames, googleMapsUrl } from '../lib/utils'

type Tab = 'unanswered' | 'all'

export function DashboardPage({
  defaultTab = 'unanswered',
  onAuthExpired,
}: {
  defaultTab?: Tab
  onAuthExpired: () => void
}) {
  const { business } = useBusiness()
  const { unanswered, reviews, stats, loading, error, isAuthError, refresh, markReplied, getReview } =
    useReviews()
  const navigate = useNavigate()

  const [tab, setTab] = useState<Tab>(defaultTab)
  const [query, setQuery] = useState('')
  const [ratingFilter, setRatingFilter] = useState<'all' | '5' | '4' | '3' | '2' | '1'>('all')

  useEffect(() => {
    if (isAuthError) {
      onAuthExpired()
      navigate('/login')
    }
  }, [isAuthError, onAuthExpired, navigate])

  const googleUrl = business ? googleMapsUrl(business.name, business.address) : undefined

  const source = tab === 'unanswered' ? unanswered : reviews
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return source.filter((r) => {
      if (ratingFilter !== 'all' && r.rating !== Number(ratingFilter)) return false
      if (!q) return true
      return (
        r.reviewer.toLowerCase().includes(q) || r.review.toLowerCase().includes(q)
      )
    })
  }, [source, query, ratingFilter])

  // Bulk reply: count each review as answered the moment it is published,
  // then re-read Google in the background when the job ends (no skeletons).
  const handleBulkPublished = useCallback(
    (ids: string[]) => markReplied(ids.map((reviewId) => ({ reviewId }))),
    [markReplied],
  )
  const handleBulkFinished = useCallback(() => void refresh({ silent: true }), [refresh])
  const reviewLabel = useCallback(
    (id: string) => {
      const r = getReview(id)
      return r ? `${r.reviewer}${r.rating ? ` (${r.rating}★)` : ''}` : undefined
    },
    [getReview],
  )

  const openReview = (r: Review) => navigate(`/reviews/${encodeURIComponent(r.review_id)}`)

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatsCard
          label="Total Reviews"
          value={loading ? '—' : stats.total_reviews}
          accent="slate"
          icon={MessagesSquare}
        />
        <StatsCard
          label="Unanswered"
          value={loading ? '—' : stats.unanswered}
          accent="rose"
          icon={Inbox}
        />
        <StatsCard
          label="Answered"
          value={loading ? '—' : stats.answered}
          accent="emerald"
          icon={MessageSquare}
        />
        <StatsCard
          label="Average Rating"
          value={loading ? '—' : stats.average_rating != null ? stats.average_rating.toFixed(1) : '—'}
          accent="amber"
          icon={Star}
          suffix={
            !loading && stats.average_rating != null ? (
              <Star className="mb-1 h-5 w-5 fill-amber-400 text-amber-400" />
            ) : undefined
          }
        />
      </div>

      {/* Bulk reply to every pending review (backend automation pipeline) */}
      <BulkReplyPanel
        locationId={business?.location_id ?? null}
        pendingCount={loading || error ? 0 : unanswered.length}
        onFinished={handleBulkFinished}
        onReviewsPublished={handleBulkPublished}
        reviewLabel={reviewLabel}
        onAuthExpired={onAuthExpired}
      />

      {/* Tabs + filters */}
      <div className="card overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-slate-100 px-5 pt-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex gap-6">
            <TabButton
              active={tab === 'unanswered'}
              onClick={() => setTab('unanswered')}
              label="Unanswered Reviews"
              count={unanswered.length}
            />
            <TabButton
              active={tab === 'all'}
              onClick={() => setTab('all')}
              label="All Reviews"
              count={reviews.length}
            />
          </div>

          <div className="flex items-center gap-2 pb-3 sm:pb-0">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                className="input !py-2 pl-9 text-sm sm:w-52"
                placeholder="Search reviews…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="relative">
              <select
                className="select !py-2 pr-8 text-sm"
                value={ratingFilter}
                onChange={(e) => setRatingFilter(e.target.value as typeof ratingFilter)}
                aria-label="Filter by rating"
              >
                <option value="all">All ratings</option>
                <option value="5">5 stars</option>
                <option value="4">4 stars</option>
                <option value="3">3 stars</option>
                <option value="2">2 stars</option>
                <option value="1">1 star</option>
              </select>
              <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            </div>
          </div>
        </div>

        {/* List */}
        {loading ? (
          <div className="p-0">
            <ReviewListSkeleton />
          </div>
        ) : error ? (
          <div className="p-5">
            <ErrorState message={error} onRetry={() => void refresh()} />
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-5">
            {query || ratingFilter !== 'all' ? (
              <EmptyState
                icon={Search}
                title="No matching reviews"
                description="Try a different search term or rating filter."
              />
            ) : tab === 'unanswered' ? (
              <EmptyState
                icon={MessagesSquare}
                title="All caught up!"
                description="There are no unanswered reviews for this business right now."
              />
            ) : (
              <EmptyState
                icon={MessagesSquare}
                title="No reviews yet"
                description="This business has no reviews on Google yet."
              />
            )}
          </div>
        ) : (
          <div>
            {filtered.map((review) => (
              <ReviewCard
                key={review.review_id}
                review={review}
                googleUrl={googleUrl}
                onOpen={() => openReview(review)}
                onReplyWithAI={!review.has_reply ? () => openReview(review) : undefined}
                showReply={tab === 'all' && review.has_reply}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function TabButton({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean
  onClick: () => void
  label: string
  count: number
}) {
  return (
    <button
      onClick={onClick}
      className={classNames(
        'relative -mb-px whitespace-nowrap border-b-2 pb-3 text-sm font-semibold transition-colors',
        active
          ? 'border-brand-600 text-brand-600'
          : 'border-transparent text-slate-500 hover:text-slate-700',
      )}
    >
      {label} ({count})
    </button>
  )
}
