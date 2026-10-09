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
import { ApiError, api, takePrefetchedReviews } from '../lib/api'
import { summarizeReviews } from '../lib/reviewStats'
import type { Review, ReviewStats } from '../lib/types'
import { useBusiness } from './BusinessContext'

interface RefreshOptions {
  /**
   * Re-fetch in the background: keep the current list, stats and counts on
   * screen (no skeletons / "—"), and keep it on a failed fetch.
   */
  silent?: boolean
}

interface ReviewsContextValue {
  reviews: Review[]
  unanswered: Review[]
  replied: Review[]
  stats: ReviewStats
  loading: boolean
  error: string | null
  isAuthError: boolean
  refresh: (opts?: RefreshOptions) => Promise<void>
  getReview: (id: string) => Review | undefined
  /**
   * Record replies we just published. Counts and lists update immediately and
   * stay updated until Google's listing confirms the reply (Google can return
   * the old, unanswered state for a short while after publishing).
   */
  markReplied: (replies: Array<{ reviewId: string; reply?: string | null }>) => void
}

const ReviewsContext = createContext<ReviewsContextValue | null>(null)

interface LocalReply {
  reply_comment: string | null
  reply_updated_at: string
}

export function ReviewsProvider({ children }: { children: ReactNode }) {
  const { business } = useBusiness()
  const locationId = business?.location_id ?? null

  const [serverReviews, setServerReviews] = useState<Review[]>([])
  const [localReplies, setLocalReplies] = useState<Record<string, LocalReply>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isAuthError, setIsAuthError] = useState(false)
  const requestId = useRef(0)

  const refresh = useCallback(
    async (opts?: RefreshOptions) => {
      const silent = !!opts?.silent
      const current = ++requestId.current
      if (!silent) {
        setLoading(true)
        setError(null)
        setIsAuthError(false)
      }
      try {
        // The first load may reuse the request App started in parallel with
        // the auth check; if that one failed, fetch again.
        const prefetched = silent ? null : takePrefetchedReviews(locationId)
        const data = prefetched
          ? await prefetched.catch(() => api.listAllReviews(locationId))
          : await api.listAllReviews(locationId)
        if (current !== requestId.current) return
        setServerReviews(data)
        setError(null)
        // Drop local replies Google now reports itself.
        setLocalReplies((pending) => {
          const confirmed = data.filter((r) => r.has_reply && pending[r.review_id])
          if (confirmed.length === 0) return pending
          const next = { ...pending }
          confirmed.forEach((r) => delete next[r.review_id])
          return next
        })
      } catch (err) {
        if (current !== requestId.current) return
        const auth = err instanceof ApiError && err.isAuth
        // A failed background refresh keeps the list on screen, unless the
        // session expired (the pages sign the user out on isAuthError).
        if (silent && !auth) return
        const message =
          err instanceof ApiError ? err.message : 'Could not load reviews. Please try again.'
        setError(message)
        setIsAuthError(auth)
      } finally {
        if (current === requestId.current) setLoading(false)
      }
    },
    [locationId],
  )

  // A new business: forget the previous one's local replies, then load.
  useEffect(() => {
    setLocalReplies({})
    void refresh()
  }, [refresh])

  const markReplied = useCallback(
    (replies: Array<{ reviewId: string; reply?: string | null }>) => {
      if (replies.length === 0) return
      const now = new Date().toISOString()
      setLocalReplies((pending) => {
        const next = { ...pending }
        replies.forEach(({ reviewId, reply }) => {
          next[reviewId] = {
            reply_comment: reply ?? pending[reviewId]?.reply_comment ?? null,
            reply_updated_at: pending[reviewId]?.reply_updated_at ?? now,
          }
        })
        return next
      })
    },
    [],
  )

  const reviews = useMemo(
    () =>
      serverReviews.map((r) => {
        const local = localReplies[r.review_id]
        if (!local || r.has_reply) return r
        return { ...r, has_reply: true, ...local }
      }),
    [serverReviews, localReplies],
  )

  const unanswered = useMemo(() => reviews.filter((r) => !r.has_reply), [reviews])
  const replied = useMemo(() => reviews.filter((r) => r.has_reply), [reviews])
  const stats = useMemo(() => summarizeReviews(reviews), [reviews])
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
      markReplied,
    }),
    [reviews, unanswered, replied, stats, loading, error, isAuthError, refresh, getReview, markReplied],
  )

  return <ReviewsContext.Provider value={value}>{children}</ReviewsContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useReviews(): ReviewsContextValue {
  const ctx = useContext(ReviewsContext)
  if (!ctx) throw new Error('useReviews must be used within a ReviewsProvider')
  return ctx
}
