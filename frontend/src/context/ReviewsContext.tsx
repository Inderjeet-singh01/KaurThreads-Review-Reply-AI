import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { ApiError, api } from '../lib/api'
import type { Review, ReviewStats } from '../lib/types'
import { useBusiness } from './BusinessContext'

interface ReviewsContextValue {
  reviews: Review[]
  unanswered: Review[]
  replied: Review[]
  stats: ReviewStats
  loading: boolean
  error: string | null
  isAuthError: boolean
  refresh: () => Promise<void>
  getReview: (id: string) => Review | undefined
  /** Merge an updated review into local state (e.g. after publishing). */
  applyReviewUpdate: (review: Review) => void
}

const ReviewsContext = createContext<ReviewsContextValue | null>(null)

function computeStats(reviews: Review[]): ReviewStats {
  const total = reviews.length
  const answered = reviews.filter((r) => r.has_reply).length
  const rated = reviews.filter((r) => r.rating != null) as Array<Review & { rating: number }>
  const average =
    rated.length > 0
      ? Math.round((rated.reduce((sum, r) => sum + r.rating, 0) / rated.length) * 10) / 10
      : null
  return { total_reviews: total, answered, unanswered: total - answered, average_rating: average }
}

export function ReviewsProvider({ children }: { children: ReactNode }) {
  const { business } = useBusiness()
  const locationId = business?.location_id ?? null

  const [reviews, setReviews] = useState<Review[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isAuthError, setIsAuthError] = useState(false)
  const requestId = useRef(0)

  const refresh = useCallback(async () => {
    const current = ++requestId.current
    setLoading(true)
    setError(null)
    setIsAuthError(false)
    try {
      const data = await api.listAllReviews(locationId)
      if (current === requestId.current) setReviews(data)
    } catch (err) {
      if (current !== requestId.current) return
      const message =
        err instanceof ApiError ? err.message : 'Could not load reviews. Please try again.'
      setError(message)
      setIsAuthError(err instanceof ApiError && err.isAuth)
    } finally {
      if (current === requestId.current) setLoading(false)
    }
  }, [locationId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const applyReviewUpdate = useCallback((updated: Review) => {
    setReviews((current) =>
      current.map((r) => (r.review_id === updated.review_id ? updated : r)),
    )
  }, [])

  const unanswered = useMemo(() => reviews.filter((r) => !r.has_reply), [reviews])
  const replied = useMemo(() => reviews.filter((r) => r.has_reply), [reviews])
  const stats = useMemo(() => computeStats(reviews), [reviews])
  const getReview = useCallback(
    (id: string) => reviews.find((r) => r.review_id === id),
    [reviews],
  )

  const value = useMemo<ReviewsContextValue>(
    () => ({
      reviews,
      unanswered,
      replied,
      stats,
      loading,
      error,
      isAuthError,
      refresh,
      getReview,
      applyReviewUpdate,
    }),
    [reviews, unanswered, replied, stats, loading, error, isAuthError, refresh, getReview, applyReviewUpdate],
  )

  return <ReviewsContext.Provider value={value}>{children}</ReviewsContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useReviews(): ReviewsContextValue {
  const ctx = useContext(ReviewsContext)
  if (!ctx) throw new Error('useReviews must be used within a ReviewsProvider')
  return ctx
}
