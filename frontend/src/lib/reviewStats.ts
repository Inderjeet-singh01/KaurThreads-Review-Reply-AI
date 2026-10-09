// Statistics derived from the review list the backend returns (GET /reviews/all).
// Everything here is computed from real review fields (rating, has_reply,
// created_at) — no illustrative numbers. Pure functions, so pages stay simple
// and the maths is unit-tested.

import type { Review, ReviewStats } from './types'
import { timestamp } from './utils'

const DAY_MS = 24 * 60 * 60 * 1000

export function summarizeReviews(reviews: Review[]): ReviewStats {
  const total = reviews.length
  const answered = reviews.filter((r) => r.has_reply).length
  const ratings = reviews.map((r) => r.rating).filter((r): r is number => r != null)
  const average =
    ratings.length > 0
      ? Math.round((ratings.reduce((sum, r) => sum + r, 0) / ratings.length) * 10) / 10
      : null
  return { total_reviews: total, answered, unanswered: total - answered, average_rating: average }
}

/** Percentage of reviews with a reply (0–100), or null when there are none. */
export function responseRate(stats: Pick<ReviewStats, 'total_reviews' | 'answered'>): number | null {
  if (stats.total_reviews === 0) return null
  return Math.round((stats.answered / stats.total_reviews) * 100)
}

export function ratedCount(reviews: Review[]): number {
  return reviews.filter((r) => r.rating != null && r.rating >= 1 && r.rating <= 5).length
}

export interface RatingBucket {
  stars: 1 | 2 | 3 | 4 | 5
  count: number
  /** Share of rated reviews (0–100, rounded). */
  percent: number
}

/** 5★ → 1★ counts and shares of the rated reviews. */
export function ratingDistribution(reviews: Review[]): RatingBucket[] {
  const counts = [0, 0, 0, 0, 0]
  reviews.forEach((r) => {
    if (r.rating != null && r.rating >= 1 && r.rating <= 5) counts[r.rating - 1] += 1
  })
  const rated = counts.reduce((a, b) => a + b, 0)
  return ([5, 4, 3, 2, 1] as const).map((stars) => ({
    stars,
    count: counts[stars - 1],
    percent: rated ? Math.round((counts[stars - 1] / rated) * 100) : 0,
  }))
}

// --- Date ranges -------------------------------------------------------------

export type DateRangeId = '7d' | '30d' | '90d' | '12m' | 'all'

export const DATE_RANGES: Array<{ id: DateRangeId; label: string; days: number | null }> = [
  { id: '7d', label: 'Last 7 days', days: 7 },
  { id: '30d', label: 'Last 30 days', days: 30 },
  { id: '90d', label: 'Last 90 days', days: 90 },
  { id: '12m', label: 'Last 12 months', days: 365 },
  { id: 'all', label: 'All time', days: null },
]

function startOfDay(time: number): number {
  const d = new Date(time)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

/** Inclusive start of a range ending today: "last 7 days" = today and the 6 before. */
export function rangeStart(days: number, now: number = Date.now()): number {
  return startOfDay(now) - (days - 1) * DAY_MS
}

/** Reviews created within the range (all of them for "All time"). */
export function reviewsInRange(reviews: Review[], range: DateRangeId, now: number = Date.now()): Review[] {
  const days = DATE_RANGES.find((r) => r.id === range)?.days ?? null
  if (days == null) return reviews
  const start = rangeStart(days, now)
  return reviews.filter((r) => {
    const t = timestamp(r.created_at)
    return t >= start && t <= now + 5 * 60 * 1000
  })
}

/** Reviews created in the last `days` days (including today). */
export function countReceivedSince(reviews: Review[], days: number, now: number = Date.now()): number {
  const start = rangeStart(days, now)
  return reviews.filter((r) => timestamp(r.created_at) >= start).length
}

// --- Trend buckets -----------------------------------------------------------

export type BucketUnit = 'day' | 'week' | 'month' | 'year'

export interface TrendBucket {
  start: number
  end: number // exclusive
  /** Short axis label ("Oct 3", "Oct", "2025"). */
  label: string
  /** Full label for tooltips and the table view. */
  fullLabel: string
  count: number
  averageRating: number | null
}

function addUnit(time: number, unit: BucketUnit, n = 1): number {
  const d = new Date(time)
  if (unit === 'day') d.setDate(d.getDate() + n)
  else if (unit === 'week') d.setDate(d.getDate() + 7 * n)
  else if (unit === 'month') d.setMonth(d.getMonth() + n)
  else d.setFullYear(d.getFullYear() + n)
  return d.getTime()
}

function startOfMonth(time: number): number {
  const d = new Date(startOfDay(time))
  d.setDate(1)
  return d.getTime()
}

function startOfYear(time: number): number {
  const d = new Date(startOfMonth(time))
  d.setMonth(0)
  return d.getTime()
}

const fmt = (time: number, options: Intl.DateTimeFormatOptions) =>
  new Date(time).toLocaleDateString('en-US', options)

function labels(start: number, end: number, unit: BucketUnit): { label: string; fullLabel: string } {
  switch (unit) {
    case 'day':
      return {
        label: fmt(start, { month: 'short', day: 'numeric' }),
        fullLabel: fmt(start, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }),
      }
    case 'week': {
      const last = end - DAY_MS
      return {
        label: fmt(start, { month: 'short', day: 'numeric' }),
        fullLabel: `${fmt(start, { month: 'short', day: 'numeric' })} – ${fmt(last, {
          month: 'short',
          day: 'numeric',
          year: 'numeric',
        })}`,
      }
    }
    case 'month':
      return {
        label: fmt(start, { month: 'short' }),
        fullLabel: fmt(start, { month: 'long', year: 'numeric' }),
      }
    default:
      return { label: fmt(start, { year: 'numeric' }), fullLabel: fmt(start, { year: 'numeric' }) }
  }
}

/**
 * Reviews received per period over the selected range. Day buckets for 7/30
 * days, weeks for 90 days, months for 12 months; "All time" spans from the
 * first review (months up to two years, years beyond).
 */
export function reviewTrend(
  reviews: Review[],
  range: DateRangeId,
  now: number = Date.now(),
): { unit: BucketUnit; buckets: TrendBucket[] } {
  let unit: BucketUnit
  let first: number
  if (range === '7d' || range === '30d') {
    unit = 'day'
    first = rangeStart(range === '7d' ? 7 : 30, now)
  } else if (range === '90d') {
    unit = 'week'
    first = rangeStart(91, now) // 13 full weeks ending today
  } else if (range === '12m') {
    unit = 'month'
    first = addUnit(startOfMonth(now), 'month', -11)
  } else {
    const times = reviews.map((r) => timestamp(r.created_at)).filter((t) => t > 0)
    if (times.length === 0) return { unit: 'month', buckets: [] }
    const oldest = Math.min(...times)
    const months =
      (new Date(now).getFullYear() - new Date(oldest).getFullYear()) * 12 +
      new Date(now).getMonth() -
      new Date(oldest).getMonth()
    unit = months < 24 ? 'month' : 'year'
    first = unit === 'month' ? startOfMonth(oldest) : startOfYear(oldest)
  }

  const buckets: TrendBucket[] = []
  for (let start = first; start <= now; start = addUnit(start, unit)) {
    const end = addUnit(start, unit)
    buckets.push({ start, end, ...labels(start, end, unit), count: 0, averageRating: null })
  }
  const sums = buckets.map(() => ({ total: 0, rated: 0 }))
  reviews.forEach((r) => {
    const t = timestamp(r.created_at)
    if (!t) return
    const i = buckets.findIndex((b) => t >= b.start && t < b.end)
    if (i < 0) return
    buckets[i].count += 1
    if (r.rating != null) {
      sums[i].total += r.rating
      sums[i].rated += 1
    }
  })
  buckets.forEach((b, i) => {
    b.averageRating = sums[i].rated ? Math.round((sums[i].total / sums[i].rated) * 10) / 10 : null
  })
  return { unit, buckets }
}
