import { describe, expect, it } from 'vitest'
import type { Review } from './types'
import {
  countReceivedSince,
  ratingDistribution,
  responseRate,
  reviewTrend,
  reviewsInRange,
  summarizeReviews,
} from './reviewStats'

const NOW = new Date(2026, 9, 9, 15, 0).getTime() // Oct 9 2026, 15:00 local

const at = (y: number, m: number, d: number, h = 12) => new Date(y, m, d, h).toISOString()

function review(id: string, created: string | null, rating: number | null, hasReply = false): Review {
  return {
    review_id: id,
    reviewer: id,
    rating,
    review: '',
    created_at: created,
    has_reply: hasReply,
    reply_comment: null,
    reply_updated_at: null,
    profile_photo_url: null,
  }
}

const REVIEWS = [
  review('a', at(2026, 9, 9), 5, true), // today
  review('b', at(2026, 9, 3), 4), // 6 days ago
  review('c', at(2026, 9, 2), 1), // 7 days ago (outside "last 7 days")
  review('d', at(2026, 8, 15), 5, true), // Sep 15
  review('e', at(2025, 11, 1), null), // Dec 2025, no rating
]

describe('summary metrics', () => {
  it('counts replies and averages only rated reviews', () => {
    expect(summarizeReviews(REVIEWS)).toEqual({
      total_reviews: 5,
      answered: 2,
      unanswered: 3,
      average_rating: 3.8, // (5+4+1+5)/4
    })
  })

  it('response rate is null without reviews, never a made-up 0%', () => {
    expect(responseRate({ total_reviews: 0, answered: 0 })).toBeNull()
    expect(responseRate({ total_reviews: 3, answered: 1 })).toBe(33)
  })

  it('rating distribution shares are of rated reviews only', () => {
    const buckets = ratingDistribution(REVIEWS)
    expect(buckets.map((b) => b.stars)).toEqual([5, 4, 3, 2, 1])
    expect(buckets[0]).toEqual({ stars: 5, count: 2, percent: 50 })
    expect(buckets[4]).toEqual({ stars: 1, count: 1, percent: 25 })
    expect(ratingDistribution([]).every((b) => b.count === 0 && b.percent === 0)).toBe(true)
  })
})

describe('date ranges', () => {
  it('"last 7 days" is today and the six days before', () => {
    expect(reviewsInRange(REVIEWS, '7d', NOW).map((r) => r.review_id)).toEqual(['a', 'b'])
    expect(countReceivedSince(REVIEWS, 30, NOW)).toBe(4)
    expect(reviewsInRange(REVIEWS, 'all', NOW)).toHaveLength(5)
  })

  it('reviews without a date are only counted in "All time"', () => {
    const undated = [...REVIEWS, review('x', null, 3)]
    expect(reviewsInRange(undated, '12m', NOW).map((r) => r.review_id)).not.toContain('x')
    expect(reviewsInRange(undated, 'all', NOW)).toHaveLength(6)
  })
})

describe('trend buckets', () => {
  it('daily buckets for 7 days, with real counts and averages', () => {
    const { unit, buckets } = reviewTrend(REVIEWS, '7d', NOW)
    expect(unit).toBe('day')
    expect(buckets).toHaveLength(7)
    expect(buckets.map((b) => b.count)).toEqual([1, 0, 0, 0, 0, 0, 1])
    expect(buckets[0].averageRating).toBe(4)
    expect(buckets[1].averageRating).toBeNull()
    expect(buckets[6].label).toBe('Oct 9')
  })

  it('weekly for 90 days and monthly for 12 months, covering every review in range', () => {
    const weeks = reviewTrend(REVIEWS, '90d', NOW)
    expect(weeks.unit).toBe('week')
    expect(weeks.buckets).toHaveLength(13)
    expect(weeks.buckets.reduce((n, b) => n + b.count, 0)).toBe(4)

    const months = reviewTrend(REVIEWS, '12m', NOW)
    expect(months.unit).toBe('month')
    expect(months.buckets).toHaveLength(12)
    expect(months.buckets[0].fullLabel).toBe('November 2025')
    expect(months.buckets.reduce((n, b) => n + b.count, 0)).toBe(5)
  })

  it('all time starts at the first review and switches to years for long histories', () => {
    expect(reviewTrend(REVIEWS, 'all', NOW).buckets[0].fullLabel).toBe('December 2025')
    const old = [...REVIEWS, review('old', at(2021, 2, 1), 4)]
    const years = reviewTrend(old, 'all', NOW)
    expect(years.unit).toBe('year')
    expect(years.buckets.map((b) => b.label)).toEqual(['2021', '2022', '2023', '2024', '2025', '2026'])
    expect(reviewTrend([], 'all', NOW).buckets).toEqual([])
  })
})
