import { useMemo, useState } from 'react'
import { CalendarDays, Inbox, MessageSquareText, Percent, Star } from 'lucide-react'
import { useReviews } from '../context/ReviewsContext'
import {
  DATE_RANGES,
  ratedCount,
  ratingDistribution,
  responseRate,
  reviewTrend,
  reviewsInRange,
  summarizeReviews,
  type DateRangeId,
} from '../lib/reviewStats'
import { useSignOutOnAuthError } from '../lib/useAuthExpiry'
import { PageHeader } from '../components/PageHeader'
import { MetricCard } from '../components/MetricCard'
import { RatingDistribution } from '../components/RatingDistribution'
import { RatingStars } from '../components/RatingStars'
import { TrendChart } from '../components/TrendChart'
import { PanelSkeleton } from '../components/Skeletons'
import { ErrorState } from '../components/ErrorState'
import { SelectField } from '../components/FormControls'

const RANGE_OPTIONS = DATE_RANGES.map((r) => ({ value: r.id, label: r.label }))

export function AnalyticsPage({ onAuthExpired }: { onAuthExpired: () => void }) {
  const { reviews, loading, error, isAuthError, refresh } = useReviews()
  useSignOutOnAuthError(isAuthError, onAuthExpired)
  const [range, setRange] = useState<DateRangeId>('30d')

  const rangeLabel = DATE_RANGES.find((r) => r.id === range)!.label
  const inRange = useMemo(() => reviewsInRange(reviews, range), [reviews, range])
  const stats = useMemo(() => summarizeReviews(inRange), [inRange])
  const trend = useMemo(() => reviewTrend(reviews, range), [reviews, range])
  const distribution = useMemo(() => ratingDistribution(inRange), [inRange])
  const rated = useMemo(() => ratedCount(inRange), [inRange])
  const rate = responseRate(stats)
  const period = range === 'all' ? 'in total' : `in the ${rangeLabel.toLowerCase()}`

  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        description="Track your reviews, ratings and response performance."
        actions={
          <div className="flex items-center gap-2">
            <CalendarDays className="hidden h-4 w-4 text-slate-400 sm:block" aria-hidden />
            <SelectField
              className="sm:w-44"
              label="Date range"
              value={range}
              onChange={setRange}
              options={RANGE_OPTIONS}
            />
          </div>
        }
      />

      {error ? (
        <ErrorState message={error} onRetry={() => void refresh()} />
      ) : (
        <>
          <section aria-label="Key metrics" className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
            <MetricCard
              label="Reviews received"
              value={stats.total_reviews.toLocaleString()}
              hint={range === 'all' ? 'All reviews on Google' : rangeLabel}
              icon={MessageSquareText}
              loading={loading}
            />
            <MetricCard
              label="Average rating"
              value={stats.average_rating != null ? stats.average_rating.toFixed(1) : '—'}
              adornment={
                stats.average_rating != null ? <RatingStars rating={stats.average_rating} size="md" /> : undefined
              }
              hint={rated ? `From ${rated.toLocaleString()} rated reviews` : 'No ratings in this period'}
              icon={Star}
              accent="green"
              loading={loading}
            />
            <MetricCard
              label="Response rate"
              value={rate != null ? `${rate}%` : '—'}
              hint={`${stats.answered.toLocaleString()} of ${stats.total_reviews.toLocaleString()} have a reply`}
              icon={Percent}
              accent="sky"
              loading={loading}
            />
            <MetricCard
              label="Needs reply"
              value={stats.unanswered.toLocaleString()}
              hint={range === 'all' ? 'All time' : rangeLabel}
              icon={Inbox}
              accent="amber"
              loading={loading}
            />
          </section>

          <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-5">
            <section className="card p-5 xl:col-span-3" aria-labelledby="trend-heading">
              <h2 id="trend-heading" className="section-title">
                Reviews over time
              </h2>
              <p className="mt-0.5 text-[13px] text-ink-muted">
                Reviews received per {trend.unit} · {rangeLabel.toLowerCase()} · by the date each review was posted
              </p>
              <div className="mt-5">
                {loading ? (
                  <PanelSkeleton lines={6} />
                ) : stats.total_reviews === 0 ? (
                  <p className="py-16 text-center text-sm text-ink-muted">
                    No reviews were received {period}.
                  </p>
                ) : (
                  <TrendChart buckets={trend.buckets} unit={trend.unit} />
                )}
              </div>
            </section>

            <section className="card p-5 xl:col-span-2" aria-labelledby="distribution-heading">
              <h2 id="distribution-heading" className="section-title">
                Rating distribution
              </h2>
              <p className="mt-0.5 text-[13px] text-ink-muted">Share of rated reviews {period}</p>
              <div className="mt-5">
                {loading ? (
                  <PanelSkeleton lines={5} />
                ) : rated === 0 ? (
                  <p className="py-16 text-center text-sm text-ink-muted">No rated reviews {period}.</p>
                ) : (
                  <RatingDistribution buckets={distribution} />
                )}
              </div>
            </section>
          </div>

          <section className="card p-5" aria-labelledby="progress-heading">
            <h2 id="progress-heading" className="section-title">
              Reply progress
            </h2>
            {loading ? (
              <PanelSkeleton lines={2} />
            ) : (
              <div className="mt-4 grid grid-cols-1 items-center gap-6 md:grid-cols-[1fr_auto]">
                <div>
                  <p className="text-sm text-slate-700">
                    <span className="text-2xl font-bold text-ink">{rate ?? 0}%</span>{' '}
                    of reviews received {period} have a reply.
                  </p>
                  <div
                    className="mt-3 h-2.5 overflow-hidden rounded-full bg-brand-50"
                    role="progressbar"
                    aria-label="Reviews with a reply"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={rate ?? 0}
                  >
                    <div className="h-full rounded-full bg-brand-500 transition-[width] duration-300" style={{ width: `${rate ?? 0}%` }} />
                  </div>
                </div>
                <dl className="grid grid-cols-2 gap-3 text-center md:w-72">
                  <div className="rounded-lg bg-slate-50 px-3 py-3">
                    <dt className="text-xs text-ink-muted">Replied</dt>
                    <dd className="text-xl font-bold text-ink">{stats.answered.toLocaleString()}</dd>
                  </div>
                  <div className="rounded-lg bg-slate-50 px-3 py-3">
                    <dt className="text-xs text-ink-muted">Needs reply</dt>
                    <dd className="text-xl font-bold text-ink">{stats.unanswered.toLocaleString()}</dd>
                  </div>
                </dl>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
