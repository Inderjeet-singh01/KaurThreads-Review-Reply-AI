import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronDown, MessagesSquare, Search } from 'lucide-react'
import { useBusiness } from '../context/BusinessContext'
import { useReviews } from '../context/ReviewsContext'
import { ReviewCard } from '../components/ReviewCard'
import { ReviewListSkeleton } from '../components/Skeletons'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { googleMapsUrl } from '../lib/utils'

type Sort = 'newest' | 'oldest' | 'highest' | 'lowest'

export function RepliedReviewsPage({ onAuthExpired }: { onAuthExpired: () => void }) {
  const { business } = useBusiness()
  const { replied, loading, error, isAuthError, refresh } = useReviews()
  const navigate = useNavigate()

  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<Sort>('newest')

  useEffect(() => {
    if (isAuthError) {
      onAuthExpired()
      navigate('/login')
    }
  }, [isAuthError, onAuthExpired, navigate])

  const googleUrl = business ? googleMapsUrl(business.name, business.address) : undefined

  const items = useMemo(() => {
    const q = query.trim().toLowerCase()
    const filtered = replied.filter(
      (r) =>
        !q ||
        r.reviewer.toLowerCase().includes(q) ||
        r.review.toLowerCase().includes(q) ||
        (r.reply_comment ?? '').toLowerCase().includes(q),
    )
    const time = (v: string | null) => (v ? new Date(v).getTime() : 0)
    return [...filtered].sort((a, b) => {
      switch (sort) {
        case 'oldest':
          return time(a.created_at) - time(b.created_at)
        case 'highest':
          return (b.rating ?? 0) - (a.rating ?? 0)
        case 'lowest':
          return (a.rating ?? 0) - (b.rating ?? 0)
        default:
          return time(b.created_at) - time(a.created_at)
      }
    })
  }, [replied, query, sort])

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-bold text-slate-900">
          Replied Reviews {!loading && `(${replied.length})`}
        </h1>
        <p className="text-sm text-slate-500">
          Reviews you’ve already answered on Google.
        </p>
      </div>

      <div className="card overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-slate-100 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative sm:w-72">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              className="input !py-2 pl-9 text-sm"
              placeholder="Search reviews…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-slate-500">Sort by</span>
            <div className="relative">
              <select
                className="select !py-2 pr-8 text-sm"
                value={sort}
                onChange={(e) => setSort(e.target.value as Sort)}
                aria-label="Sort replied reviews"
              >
                <option value="newest">Newest</option>
                <option value="oldest">Oldest</option>
                <option value="highest">Highest rating</option>
                <option value="lowest">Lowest rating</option>
              </select>
              <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            </div>
          </div>
        </div>

        {loading ? (
          <ReviewListSkeleton />
        ) : error ? (
          <div className="p-5">
            <ErrorState message={error} onRetry={refresh} />
          </div>
        ) : items.length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={MessagesSquare}
              title={query ? 'No matching replies' : 'No replied reviews yet'}
              description={
                query
                  ? 'Try a different search term.'
                  : 'Once you reply to reviews, they’ll appear here.'
              }
            />
          </div>
        ) : (
          <div>
            {items.map((review) => (
              <ReviewCard
                key={review.review_id}
                review={review}
                googleUrl={googleUrl}
                onOpen={() => navigate(`/reviews/${encodeURIComponent(review.review_id)}`)}
                showReply
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
