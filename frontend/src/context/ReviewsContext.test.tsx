import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { api } from '../lib/api'
import type { Review } from '../lib/types'
import { BusinessProvider } from './BusinessContext'
import { ReviewsProvider, useReviews } from './ReviewsContext'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return { ...actual, api: { listAllReviews: vi.fn() } }
})

const mocked = vi.mocked(api)

const review = (id: string, overrides: Partial<Review> = {}): Review => ({
  review_id: id,
  reviewer: `Reviewer ${id}`,
  rating: 5,
  review: 'Lovely store',
  created_at: '2026-09-01T10:00:00Z',
  has_reply: false,
  reply_comment: null,
  reply_updated_at: null,
  profile_photo_url: null,
  ...overrides,
})

let ctx: ReturnType<typeof useReviews>
function Probe() {
  ctx = useReviews()
  return (
    <p>
      {ctx.loading ? 'loading' : `unanswered=${ctx.stats.unanswered} answered=${ctx.stats.answered}`}
    </p>
  )
}

function renderContext() {
  render(
    <BusinessProvider>
      <ReviewsProvider>
        <Probe />
      </ReviewsProvider>
    </BusinessProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ReviewsContext counts after publishing', () => {
  it('counts a published review as answered at once, even if Google is still stale', async () => {
    mocked.listAllReviews.mockResolvedValue([review('a'), review('b')])
    renderContext()
    expect(await screen.findByText('unanswered=2 answered=0')).toBeTruthy()

    act(() => ctx.markReplied([{ reviewId: 'a', reply: 'Thank you!' }]))
    expect(screen.getByText('unanswered=1 answered=1')).toBeTruthy()
    expect(ctx.getReview('a')?.reply_comment).toBe('Thank you!')
    expect(ctx.getReview('a')?.reply_updated_at).toBeTruthy()

    // Google's listing has not caught up yet: the count must not jump back.
    await act(() => ctx.refresh({ silent: true }))
    expect(screen.getByText('unanswered=1 answered=1')).toBeTruthy()

    // Google confirms; its own reply data wins.
    mocked.listAllReviews.mockResolvedValue([
      review('a', { has_reply: true, reply_comment: 'Thank you!', reply_updated_at: '2026-10-01T12:00:00Z' }),
      review('b'),
    ])
    await act(() => ctx.refresh({ silent: true }))
    expect(ctx.getReview('a')?.reply_updated_at).toBe('2026-10-01T12:00:00Z')
    expect(screen.getByText('unanswered=1 answered=1')).toBeTruthy()
  })

  it('a silent refresh keeps the counts on screen and survives a failed fetch', async () => {
    mocked.listAllReviews.mockResolvedValue([review('a')])
    renderContext()
    expect(await screen.findByText('unanswered=1 answered=0')).toBeTruthy()

    let resolve!: (value: Review[]) => void
    mocked.listAllReviews.mockReturnValueOnce(new Promise((r) => (resolve = r)))
    let pending!: Promise<void>
    act(() => {
      pending = ctx.refresh({ silent: true })
    })
    expect(screen.getByText('unanswered=1 answered=0')).toBeTruthy() // no "loading"
    resolve([review('a', { has_reply: true, reply_comment: 'Hi' })])
    await act(() => pending)
    expect(screen.getByText('unanswered=0 answered=1')).toBeTruthy()

    mocked.listAllReviews.mockRejectedValueOnce(new Error('network'))
    await act(() => ctx.refresh({ silent: true }))
    await waitFor(() => expect(ctx.error).toBeNull())
    expect(screen.getByText('unanswered=0 answered=1')).toBeTruthy()
  })
})
