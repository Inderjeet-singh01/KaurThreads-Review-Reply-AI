import { useCallback, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Inbox, MousePointerClick, PartyPopper, RefreshCw, SearchX } from 'lucide-react'
import { useBusiness } from '../context/BusinessContext'
import { useReviews } from '../context/ReviewsContext'
import type { Review } from '../lib/types'
import { classNames, timestamp } from '../lib/utils'
import { useSignOutOnAuthError } from '../lib/useAuthExpiry'
import { PageHeader } from '../components/PageHeader'
import { BulkReplyPanel } from '../components/BulkReplyPanel'
import { ReviewListItem } from '../components/ReviewListItem'
import { ReplyWorkspace } from '../components/ReplyWorkspace'
import { ReviewListSkeleton, PanelSkeleton } from '../components/Skeletons'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { SearchInput, SegmentedControl, SelectField } from '../components/FormControls'

type View = 'pending' | 'all' | 'replied'
type RatingFilter = 'all' | '5' | '4' | '3' | '2' | '1'
type Sort = 'newest' | 'oldest' | 'highest' | 'lowest'

const RATING_OPTIONS: ReadonlyArray<{ value: RatingFilter; label: string }> = [
  { value: 'all', label: 'All ratings' },
  { value: '5', label: '5 stars' },
  { value: '4', label: '4 stars' },
  { value: '3', label: '3 stars' },
  { value: '2', label: '2 stars' },
  { value: '1', label: '1 star' },
]

const SORT_OPTIONS: ReadonlyArray<{ value: Sort; label: string }> = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'highest', label: 'Highest rating' },
  { value: 'lowest', label: 'Lowest rating' },
]

function sortReviews(list: Review[], sort: Sort): Review[] {
  return [...list].sort((a, b) => {
    switch (sort) {
      case 'oldest':
        return timestamp(a.created_at) - timestamp(b.created_at)
      case 'highest':
        return (b.rating ?? 0) - (a.rating ?? 0) || timestamp(b.created_at) - timestamp(a.created_at)
      case 'lowest':
        return (a.rating ?? 0) - (b.rating ?? 0) || timestamp(b.created_at) - timestamp(a.created_at)
      default:
        return timestamp(b.created_at) - timestamp(a.created_at)
    }
  })
}

const reviewPath = (id: string) => `/reviews/${encodeURIComponent(id)}`

/**
 * Review Inbox: filterable list + reply workspace side by side on desktop;
 * on small screens the list and the selected review are separate steps.
 * Serves /reviews and /reviews/:reviewId (direct links keep working).
 */
export function ReviewInboxPage({ onAuthExpired }: { onAuthExpired: () => void }) {
  const { reviewId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const { business } = useBusiness()
  const {
    reviews,
    unanswered,
    replied,
    loading,
    error,
    isAuthError,
    refresh,
    getReview,
    markReplied,
  } = useReviews()
  useSignOutOnAuthError(isAuthError, onAuthExpired)

  const [view, setView] = useState<View>('pending')
  const [query, setQuery] = useState('')
  const [rating, setRating] = useState<RatingFilter>('all')
  const [sort, setSort] = useState<Sort>('newest')
  const [refreshing, setRefreshing] = useState(false)
  // Overview's "Reply to pending reviews" opens the bulk confirmation once.
  const [bulkRequested] = useState(
    () => !!(location.state as { openBulk?: boolean } | null)?.openBulk,
  )

  const source = view === 'pending' ? unanswered : view === 'replied' ? replied : reviews
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const matches = source.filter((r) => {
      if (rating !== 'all' && r.rating !== Number(rating)) return false
      if (!q) return true
      return (
        r.reviewer.toLowerCase().includes(q) ||
        r.review.toLowerCase().includes(q) ||
        (r.reply_comment ?? '').toLowerCase().includes(q)
      )
    })
    return sortReviews(matches, sort)
  }, [source, query, rating, sort])

  // Explicitly opened review (URL), else the first one in the list on desktop.
  const opened = reviewId ? getReview(reviewId) : undefined
  const selected = reviewId ? opened : filtered[0]
  const filtersActive = query.trim() !== '' || rating !== 'all'

  const nextPending = useMemo(
    () => sortReviews(unanswered, 'newest').find((r) => r.review_id !== selected?.review_id),
    [unanswered, selected],
  )
  const goNext = useCallback(() => {
    if (nextPending) navigate(reviewPath(nextPending.review_id))
  }, [navigate, nextPending])

  // Bulk reply: count each review as answered the moment it is published,
  // then re-read Google in the background when the job ends.
  const handleBulkPublished = useCallback(
    (ids: string[]) => markReplied(ids.map((id) => ({ reviewId: id }))),
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

  const reload = async () => {
    setRefreshing(true)
    try {
      await refresh(error ? undefined : { silent: true })
    } finally {
      setRefreshing(false)
    }
  }

  const clearFilters = () => {
    setQuery('')
    setRating('all')
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Review Inbox"
        description={
          loading
            ? 'Loading your reviews…'
            : unanswered.length === 0
              ? 'Every review has a reply.'
              : `${unanswered.length} ${unanswered.length === 1 ? 'review needs' : 'reviews need'} a reply.`
        }
        actions={
          <button
            className="btn-secondary px-3 sm:px-4"
            onClick={() => void reload()}
            disabled={refreshing || loading}
            aria-label="Refresh reviews"
          >
            <RefreshCw className={classNames('h-4 w-4', (refreshing || loading) && 'animate-spin')} aria-hidden />
            <span className="hidden sm:inline">Refresh</span>
          </button>
        }
      />

      {/* On small screens the bulk bar belongs to the list step, not a single review. */}
      <div className={reviewId ? 'hidden lg:block' : undefined}>
        <BulkReplyPanel
        locationId={business?.location_id ?? null}
        pendingCount={loading || error ? 0 : unanswered.length}
        onFinished={handleBulkFinished}
        onReviewsPublished={handleBulkPublished}
        reviewLabel={reviewLabel}
        onAuthExpired={onAuthExpired}
        autoOpen={bulkRequested}
        />
      </div>

      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(300px,380px)_minmax(0,1fr)]">
        {/* Review list */}
        <section
          aria-label="Reviews"
          className={classNames(
            'card flex-col overflow-hidden lg:sticky lg:top-6 lg:flex lg:max-h-[calc(100vh-3rem)]',
            reviewId ? 'hidden' : 'flex',
          )}
        >
          <div className="space-y-3 border-b border-line p-4">
            <div className="scroll-area -mx-1 overflow-x-auto px-1">
              <SegmentedControl
                label="Show"
                value={view}
                onChange={setView}
                options={[
                  { value: 'pending', label: 'Needs reply', count: loading ? undefined : unanswered.length },
                  { value: 'all', label: 'All', count: loading ? undefined : reviews.length },
                  { value: 'replied', label: 'Replied', count: loading ? undefined : replied.length },
                ]}
              />
            </div>
            <SearchInput
              label="Search reviews"
              placeholder="Search reviewer or text…"
              value={query}
              onChange={setQuery}
            />
            <div className="grid grid-cols-2 gap-2">
              <SelectField label="Filter by rating" value={rating} onChange={setRating} options={RATING_OPTIONS} />
              <SelectField label="Sort reviews" value={sort} onChange={setSort} options={SORT_OPTIONS} />
            </div>
          </div>

          <div className="scroll-area min-h-0 flex-1 overflow-y-auto">
            {loading ? (
              <ReviewListSkeleton />
            ) : error ? (
              <ErrorState compact message={error} onRetry={() => void refresh()} />
            ) : filtered.length === 0 ? (
              filtersActive ? (
                <EmptyState
                  compact
                  icon={SearchX}
                  title="No matching reviews"
                  description="Try a different search or rating filter."
                  action={
                    <button className="btn-secondary btn-sm" onClick={clearFilters}>
                      Clear filters
                    </button>
                  }
                />
              ) : view === 'pending' ? (
                <EmptyState
                  compact
                  icon={PartyPopper}
                  title="All caught up!"
                  description="There are no reviews waiting for a reply."
                />
              ) : (
                <EmptyState
                  compact
                  icon={Inbox}
                  title={view === 'replied' ? 'No replied reviews yet' : 'No reviews yet'}
                  description={
                    view === 'replied'
                      ? 'Reviews you answer will appear here.'
                      : 'This business has no Google reviews yet.'
                  }
                />
              )
            ) : (
              <>
                <p className="sr-only" aria-live="polite">
                  {filtered.length} {filtered.length === 1 ? 'review' : 'reviews'} shown
                </p>
                {filtered.map((review) => (
                  <ReviewListItem
                    key={review.review_id}
                    review={review}
                    to={reviewPath(review.review_id)}
                    selected={review.review_id === selected?.review_id}
                    hideStatus={view === 'pending'}
                  />
                ))}
              </>
            )}
          </div>
        </section>

        {/* Selected review + reply workspace */}
        <div className={classNames('min-w-0 lg:block', reviewId ? 'block' : 'hidden')}>
          {reviewId && (
            <Link
              to="/reviews"
              className="focus-ring mb-3 inline-flex items-center gap-1.5 rounded-md text-sm font-medium text-slate-600 hover:text-brand-700 lg:hidden"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden />
              Back to inbox
            </Link>
          )}
          {loading && !selected ? (
            <div className="card">
              <PanelSkeleton lines={5} />
            </div>
          ) : reviewId && !opened ? (
            <EmptyState
              icon={SearchX}
              title="Review not found"
              description="It may have been removed, or it belongs to another business."
              action={
                <Link to="/reviews" className="btn-primary">
                  Back to inbox
                </Link>
              }
            />
          ) : selected ? (
            <ReplyWorkspace
              key={selected.review_id}
              review={selected}
              autoGenerate={!!reviewId}
              onAuthExpired={onAuthExpired}
              onNext={nextPending ? goNext : undefined}
            />
          ) : error ? null : (
            <EmptyState
              icon={MousePointerClick}
              title="Select a review"
              description="Choose a review from the list to read it and draft a reply."
            />
          )}
        </div>
      </div>
    </div>
  )
}
