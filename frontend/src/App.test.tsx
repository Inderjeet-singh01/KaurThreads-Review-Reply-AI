import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App from './App'
import { BusinessProvider } from './context/BusinessContext'
import { ToastProvider } from './context/ToastContext'
import { discardPrefetchedReviews } from './lib/api'
import type { AuthStatus, Review } from './lib/types'

// The real API layer runs; only the network (fetch) is faked, so request
// de-duplication and the review prefetch are exercised too.

const BUSINESS = {
  location_id: 'loc1',
  name: 'KaurThreads',
  address: '',
  total_reviews: 1,
  answered: 1,
  unanswered: 0,
  average_rating: 5,
}

const REVIEWS: Review[] = [
  {
    review_id: 'r1',
    reviewer: 'Simran Protected',
    rating: 5,
    review: 'Beautiful suits',
    created_at: '2026-09-01T10:00:00Z',
    has_reply: true,
    reply_comment: 'Thank you!',
    reply_updated_at: '2026-09-02T10:00:00Z',
    profile_photo_url: null,
  },
]

type Responder = () => Promise<Response> | Response

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

let statusResponder: Responder
let calls: string[]

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

beforeEach(() => {
  calls = []
  localStorage.setItem('rra.selectedBusiness', JSON.stringify(BUSINESS))
  statusResponder = () => json({ authenticated: true } satisfies AuthStatus)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const path = new URL(url).pathname
      calls.push(path)
      if (path === '/auth/google/status') return statusResponder()
      if (path === '/reviews/all') return json(REVIEWS)
      return json({ detail: 'not mocked' }, 404)
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  localStorage.clear()
  discardPrefetchedReviews()
})

function renderApp(path = '/replied', { strict = false } = {}) {
  const tree = (
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <BusinessProvider>
          <App retryDelaysMs={[0, 0]} />
        </BusinessProvider>
      </ToastProvider>
    </MemoryRouter>
  )
  return render(strict ? <StrictMode>{tree}</StrictMode> : tree)
}

const count = (path: string) => calls.filter((p) => p === path).length

describe('App startup', () => {
  it('shows the branded startup screen immediately and no review data before auth', async () => {
    const status = deferred<Response>()
    statusResponder = () => status.promise
    renderApp()

    expect(screen.getByText('Review Reply AI')).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('Checking your Google connection')
    // The review list was fetched in parallel, but is not shown yet.
    await waitFor(() => expect(count('/reviews/all')).toBe(1))
    expect(screen.queryByText('Simran Protected')).toBeNull()

    await act(async () => status.resolve(json({ authenticated: true })))
    expect(await screen.findByText('Simran Protected')).toBeTruthy()
    expect(count('/reviews/all')).toBe(1) // the prefetch was reused, not repeated
    expect(count('/auth/google/status')).toBe(1)
  })

  it('sends the status check once under React StrictMode', async () => {
    renderApp('/replied', { strict: true })
    expect(await screen.findByText('Simran Protected')).toBeTruthy()
    expect(count('/auth/google/status')).toBe(1)
  })

  it('sends GET requests without Content-Type (no CORS preflight)', async () => {
    renderApp()
    await screen.findByText('Simran Protected')
    const fetchMock = vi.mocked(fetch)
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).has('Content-Type')).toBe(false)
    }
  })

  it('keeps protected routes behind the login page when not connected', async () => {
    statusResponder = () => json({ authenticated: false, retryable: false })
    renderApp('/replied')
    expect(await screen.findByText('Manage Your Google Reviews with AI')).toBeTruthy()
    expect(screen.queryByText('Simran Protected')).toBeNull()
  })

  it('retries a transient failure instead of signing the user out', async () => {
    let attempts = 0
    statusResponder = () => {
      attempts += 1
      if (attempts === 1) throw new TypeError('Failed to fetch')
      return json({ authenticated: true })
    }
    renderApp()
    expect(await screen.findByText('Simran Protected')).toBeTruthy()
    expect(attempts).toBe(2)
    expect(screen.queryByText('Manage Your Google Reviews with AI')).toBeNull()
  })

  it('shows a retry screen (not the login page) when the server stays unavailable', async () => {
    statusResponder = () => json({ detail: 'Bad gateway' }, 502)
    renderApp()
    expect((await screen.findByRole('alert')).textContent).toContain(
      'Your Google connection has not changed',
    )
    expect(count('/auth/google/status')).toBe(3) // first try + 2 automatic retries
    expect(screen.queryByText('Manage Your Google Reviews with AI')).toBeNull()
    expect(screen.queryByText('Simran Protected')).toBeNull()

    statusResponder = () => json({ authenticated: true })
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(await screen.findByText('Simran Protected')).toBeTruthy()
  })

  it('treats "could not check" from the server as temporary', async () => {
    statusResponder = () =>
      json({ authenticated: false, retryable: true, reason: 'database unavailable' })
    renderApp()
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.queryByText('Manage Your Google Reviews with AI')).toBeNull()
  })

  it('a 401 from the status check means not connected', async () => {
    statusResponder = () => json({ detail: 'expired' }, 401)
    renderApp()
    expect(await screen.findByText('Manage Your Google Reviews with AI')).toBeTruthy()
  })

  it('explains a slow wake-up after a few seconds', async () => {
    vi.useFakeTimers()
    const status = deferred<Response>()
    statusResponder = () => status.promise // a sleeping backend
    renderApp()
    expect(screen.queryByText(/Waking up the server/)).toBeNull()
    await act(async () => {
      vi.advanceTimersByTime(4000)
    })
    expect(screen.getByText(/Waking up the server/)).toBeTruthy()
    await act(async () => status.resolve(json({ authenticated: false })))
  })

  it('does not prefetch reviews on the login page', async () => {
    statusResponder = () => json({ authenticated: false })
    renderApp('/login')
    await screen.findByText('Manage Your Google Reviews with AI')
    expect(count('/reviews/all')).toBe(0)
  })
})
