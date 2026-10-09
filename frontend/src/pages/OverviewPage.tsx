import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  BarChart3,
  Bot,
  ChevronRight,
  Inbox,
  Layers,
  MessageSquareText,
  PartyPopper,
  Percent,
  Star,
  type LucideIcon,
} from 'lucide-react'
import { useReviews } from '../context/ReviewsContext'
import { countReceivedSince, ratedCount, ratingDistribution, responseRate } from '../lib/reviewStats'
import { classNames, timestamp } from '../lib/utils'
import { useSignOutOnAuthError } from '../lib/useAuthExpiry'
import { PageHeader } from '../components/PageHeader'
import { MetricCard } from '../components/MetricCard'
import { RatingDistribution } from '../components/RatingDistribution'
import { RatingStars } from '../components/RatingStars'
import { ReviewListItem } from '../components/ReviewListItem'
import { ReviewListSkeleton, PanelSkeleton } from '../components/Skeletons'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'

const RECENT_LIMIT = 5

export function OverviewPage({ onAuthExpired }: { onAuthExpired: () => void }) {
  const { reviews, unanswered, stats, loading, error, isAuthError, refresh } = useReviews()
  useSignOutOnAuthError(isAuthError, onAuthExpired)

  const recent = useMemo(
    () =>
      [...unanswered]
        .sort((a, b) => timestamp(b.created_at) - timestamp(a.created_at))
        .slice(0, RECENT_LIMIT),
    [unanswered],
  )
  const distribution = useMemo(() => ratingDistribution(reviews), [reviews])
  const rated = useMemo(() => ratedCount(reviews), [reviews])
  const lastMonth = useMemo(() => countReceivedSince(reviews, 30), [reviews])
  const rate = responseRate(stats)

  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        description="Your Google reviews at a glance — monitor performance and keep your reputation healthy."
      />

      {error ? (
        <ErrorState message={error} onRetry={() => void refresh()} />
      ) : (
        <>
          <section aria-label="Key metrics" className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
            <MetricCard
              label="Total reviews"
              value={stats.total_reviews.toLocaleString()}
              hint={`${lastMonth.toLocaleString()} in the last 30 days`}
              icon={MessageSquareText}
              loading={loading}
            />
            <MetricCard
              label="Needs reply"
              value={stats.unanswered.toLocaleString()}
              hint={stats.unanswered ? 'Waiting for your reply' : 'You’re all caught up'}
              icon={Inbox}
              accent="amber"
              loading={loading}
            />
            <MetricCard
              label="Average rating"
              value={stats.average_rating != null ? stats.average_rating.toFixed(1) : '—'}
              adornment={
                stats.average_rating != null ? <RatingStars rating={stats.average_rating} size="md" /> : undefined
              }
              hint={rated ? `Based on ${rated.toLocaleString()} ratings` : 'No ratings yet'}
              icon={Star}
              accent="green"
              loading={loading}
            />
            <MetricCard
              label="Response rate"
              value={rate != null ? `${rate}%` : '—'}
              hint={`Replied to ${stats.answered.toLocaleString()} of ${stats.total_reviews.toLocaleString()}`}
              icon={Percent}
              accent="sky"
              loading={loading}
            />
          </section>

          <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-12">
            {/* Recent reviews needing a reply */}
            <section className="card flex flex-col xl:col-span-5" aria-labelledby="recent-heading">
              <div className="flex items-center justify-between gap-3 px-5 pb-3 pt-5">
                <h2 id="recent-heading" className="section-title">
                  Recent reviews needing a reply
                </h2>
                <Link to="/reviews" className="focus-ring shrink-0 rounded-md text-[13px] font-semibold text-brand-700 hover:text-brand-800">
                  View all
                </Link>
              </div>
              <div className="flex-1 border-t border-line">
                {loading ? (
                  <ReviewListSkeleton count={3} />
                ) : recent.length === 0 ? (
                  <EmptyState
                    compact
                    icon={PartyPopper}
                    title="All caught up"
                    description="Every review for this business has a reply."
                  />
                ) : (
                  recent.map((review) => (
                    <ReviewListItem
                      key={review.review_id}
                      review={review}
                      to={`/reviews/${encodeURIComponent(review.review_id)}`}
                      hideStatus
                    />
                  ))
                )}
              </div>
              {!loading && unanswered.length > RECENT_LIMIT && (
                <p className="border-t border-line px-5 py-3 text-[13px] text-ink-muted">
                  {unanswered.length - RECENT_LIMIT} more in the{' '}
                  <Link to="/reviews" className="font-semibold text-brand-700 hover:text-brand-800">
                    Review Inbox
                  </Link>
                </p>
              )}
            </section>

            {/* Rating insights */}
            <section className="card p-5 xl:col-span-4" aria-labelledby="insights-heading">
              <h2 id="insights-heading" className="section-title">
                Review insights
              </h2>
              <p className="mt-0.5 text-[13px] text-ink-muted">Share of rated reviews by star rating</p>
              <div className="mt-5">
                {loading ? (
                  <PanelSkeleton lines={5} />
                ) : rated === 0 ? (
                  <p className="py-8 text-center text-sm text-ink-muted">No rated reviews yet.</p>
                ) : (
                  <RatingDistribution buckets={distribution} />
                )}
              </div>
            </section>

            {/* Quick actions */}
            <section className="xl:col-span-3" aria-labelledby="actions-heading">
              <h2 id="actions-heading" className="section-title mb-3 px-1 xl:mt-1">
                Quick actions
              </h2>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-1">
                <QuickAction
                  primary
                  icon={Layers}
                  title="Reply to pending reviews"
                  description="Bulk reply — asks you to confirm first"
                  to="/reviews"
                  state={{ openBulk: true }}
                  disabled={loading || stats.unanswered === 0}
                />
                <QuickAction icon={Inbox} title="Open Review Inbox" description="Read and answer reviews" to="/reviews" />
                <QuickAction icon={Bot} title="Automation" description="Automatic replies status" to="/automation" />
                <QuickAction icon={BarChart3} title="View analytics" description="Trends and ratings" to="/analytics" />
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  )
}

function QuickAction({
  icon: Icon,
  title,
  description,
  to,
  state,
  primary,
  disabled,
}: {
  icon: LucideIcon
  title: string
  description: string
  to: string
  state?: unknown
  primary?: boolean
  disabled?: boolean
}) {
  const className = classNames(
    'focus-ring group flex w-full items-center gap-3 rounded-xl px-4 py-3.5 text-left transition-colors',
    primary
      ? 'bg-brand-600 text-white shadow-sm hover:bg-brand-700'
      : 'border border-line bg-white shadow-card hover:border-brand-200 hover:bg-brand-50/40',
    disabled && 'pointer-events-none opacity-60',
  )
  const content = (
    <>
      <span
        className={classNames(
          'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
          primary
            ? 'bg-white/15'
            : 'bg-slate-100 text-slate-600 group-hover:bg-brand-100 group-hover:text-brand-700',
        )}
        aria-hidden
      >
        <Icon className="h-[18px] w-[18px]" />
      </span>
      <span className="min-w-0 flex-1">
        <span className={classNames('block text-sm font-semibold', !primary && 'text-ink')}>{title}</span>
        <span className={classNames('block text-xs', primary ? 'text-brand-100' : 'text-ink-muted')}>
          {description}
        </span>
      </span>
      <ChevronRight className={classNames('h-4 w-4', primary ? 'text-brand-100' : 'text-slate-400')} aria-hidden />
    </>
  )
  if (disabled) {
    return (
      <div className={className} aria-disabled="true">
        {content}
      </div>
    )
  }
  return (
    <Link to={to} state={state} className={className}>
      {content}
    </Link>
  )
}
