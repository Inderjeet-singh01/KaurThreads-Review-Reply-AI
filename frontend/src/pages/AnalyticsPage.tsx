import { useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { MessageSquare, Percent, Star, TrendingUp } from 'lucide-react'
import { useReviews } from '../context/ReviewsContext'
import { StatsCard } from '../components/StatsCard'
import { RatingStars } from '../components/RatingStars'
import { ReviewListSkeleton } from '../components/Skeletons'
import { ErrorState } from '../components/ErrorState'

export function AnalyticsPage({ onAuthExpired }: { onAuthExpired: () => void }) {
  const { reviews, stats, loading, error, isAuthError, refresh } = useReviews()
  const navigate = useNavigate()

  useEffect(() => {
    if (isAuthError) {
      onAuthExpired()
      navigate('/login')
    }
  }, [isAuthError, onAuthExpired, navigate])

  const responseRate =
    stats.total_reviews > 0
      ? Math.round((stats.answered / stats.total_reviews) * 100)
      : 0

  const distribution = useMemo(() => {
    const counts = [0, 0, 0, 0, 0] // index 0 => 1 star
    reviews.forEach((r) => {
      if (r.rating && r.rating >= 1 && r.rating <= 5) counts[r.rating - 1] += 1
    })
    const max = Math.max(1, ...counts)
    return { counts, max }
  }, [reviews])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-bold text-slate-900">Analytics</h1>
        <p className="text-sm text-slate-500">
          An overview of your review performance for this business.
        </p>
      </div>

      {loading ? (
        <ReviewListSkeleton count={3} />
      ) : error ? (
        <ErrorState message={error} onRetry={refresh} />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatsCard label="Total Reviews" value={stats.total_reviews} icon={MessageSquare} />
            <StatsCard
              label="Response Rate"
              value={`${responseRate}%`}
              accent="emerald"
              icon={Percent}
            />
            <StatsCard
              label="Unanswered"
              value={stats.unanswered}
              accent="rose"
              icon={TrendingUp}
            />
            <StatsCard
              label="Average Rating"
              value={stats.average_rating != null ? stats.average_rating.toFixed(1) : '—'}
              accent="amber"
              icon={Star}
            />
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {/* Rating distribution */}
            <section className="card p-5">
              <h2 className="text-sm font-bold text-slate-900">Rating Distribution</h2>
              <div className="mt-4 space-y-3">
                {[5, 4, 3, 2, 1].map((stars) => {
                  const count = distribution.counts[stars - 1]
                  const pct = Math.round((count / distribution.max) * 100)
                  return (
                    <div key={stars} className="flex items-center gap-3">
                      <div className="flex w-10 items-center gap-1 text-xs font-medium text-slate-500">
                        {stars}
                        <Star className="h-3 w-3 fill-amber-400 text-amber-400" />
                      </div>
                      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full bg-amber-400 transition-all"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className="w-8 text-right text-xs font-semibold text-slate-600">
                        {count}
                      </span>
                    </div>
                  )
                })}
              </div>
            </section>

            {/* Reply progress */}
            <section className="card p-5">
              <h2 className="text-sm font-bold text-slate-900">Reply Progress</h2>
              <div className="mt-6 flex flex-col items-center">
                <div className="flex items-center gap-2">
                  <span className="text-4xl font-bold text-slate-900">{responseRate}%</span>
                </div>
                <p className="mt-1 text-sm text-slate-500">of reviews answered</p>
                <div className="mt-5 h-3 w-full overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-emerald-500 transition-all"
                    style={{ width: `${responseRate}%` }}
                  />
                </div>
                <div className="mt-5 grid w-full grid-cols-2 gap-4 text-center">
                  <div className="rounded-lg bg-emerald-50 py-3">
                    <p className="text-2xl font-bold text-emerald-600">{stats.answered}</p>
                    <p className="text-xs text-slate-500">Answered</p>
                  </div>
                  <div className="rounded-lg bg-rose-50 py-3">
                    <p className="text-2xl font-bold text-rose-600">{stats.unanswered}</p>
                    <p className="text-xs text-slate-500">Unanswered</p>
                  </div>
                </div>
                {stats.average_rating != null && (
                  <div className="mt-5 flex items-center gap-2">
                    <RatingStars rating={Math.round(stats.average_rating)} size="md" />
                    <span className="text-sm font-semibold text-slate-700">
                      {stats.average_rating.toFixed(1)} average
                    </span>
                  </div>
                )}
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  )
}
