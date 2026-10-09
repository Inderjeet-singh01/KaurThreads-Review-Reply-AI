/* eslint-disable react-refresh/only-export-components -- test helpers, not hot-reloaded */
// Test helpers: a fake backend behind `fetch` (the real API layer, contexts
// and routing run unchanged) and a full-app render at a given URL.

import { render } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { vi } from 'vitest'
import App from '../App'
import { BusinessProvider } from '../context/BusinessContext'
import { ToastProvider } from '../context/ToastContext'
import { discardPrefetchedReviews } from '../lib/api'
import type { AutomationStatus, LocationSummary, Review } from '../lib/types'
import { clearReplyDrafts } from '../components/ReplyWorkspace'
import { resetBusinessSwitcherCache } from '../components/BusinessSwitcher'

export const BUSINESS: LocationSummary = {
  location_id: 'loc1',
  name: 'Kaur Threads',
  address: '12 Market Street, Amritsar',
  total_reviews: 3,
  answered: 1,
  unanswered: 2,
  average_rating: 4.3,
}

export const AUTOMATION: AutomationStatus = {
  enabled: true,
  dry_run: false,
  max_regenerations: 1,
  max_processing_attempts: 3,
  location_ids: [],
  webhook_auth_configured: true,
  test_endpoint_enabled: false,
  backfill_delay_seconds: 2,
}

export function review(id: string, overrides: Partial<Review> = {}): Review {
  return {
    review_id: id,
    reviewer: `Reviewer ${id}`,
    rating: 5,
    review: `Review text ${id}`,
    created_at: '2026-10-01T10:00:00Z',
    has_reply: false,
    reply_comment: null,
    reply_updated_at: null,
    profile_photo_url: null,
    ...overrides,
  }
}

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

export interface FakeRequest {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
}

type Handler = (req: FakeRequest) => Response | Promise<Response>

/**
 * Route `fetch` to handlers keyed "METHOD /path" (":param" segments match
 * anything). Every request is recorded in `calls`.
 */
export function installFakeBackend(
  reviews: Review[],
  overrides: Record<string, Handler> = {},
) {
  const calls: FakeRequest[] = []
  const routes: Record<string, Handler> = {
    'GET /auth/google/status': () => json({ authenticated: true }),
    'GET /reviews/all': () => json(reviews),
    'GET /automation/status': () => json(AUTOMATION),
    'GET /automation/backfill': () => json({ job: null }),
    'GET /locations': () => json([BUSINESS]),
    ...overrides,
  }
  const matchers = Object.entries(routes).map(([key, handler]) => {
    const [method, pattern] = key.split(' ')
    const regex = new RegExp(`^${pattern.replace(/:[^/]+/g, '[^/]+')}$`)
    return { method, regex, handler }
  })

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input))
      const req: FakeRequest = {
        method: (init?.method ?? 'GET').toUpperCase(),
        path: url.pathname,
        query: url.searchParams,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      }
      calls.push(req)
      const match = matchers.find((m) => m.method === req.method && m.regex.test(req.path))
      return match ? match.handler(req) : json({ detail: `not mocked: ${req.method} ${req.path}` }, 404)
    }),
  )

  return {
    calls,
    count: (method: string, path: string | RegExp) =>
      calls.filter(
        (c) => c.method === method && (typeof path === 'string' ? c.path === path : path.test(c.path)),
      ).length,
  }
}

function CurrentLocation() {
  const { pathname } = useLocation()
  return <span data-testid="location">{pathname}</span>
}

export function renderApp(path: string, state?: unknown) {
  localStorage.setItem('rra.selectedBusiness', JSON.stringify(BUSINESS))
  return render(
    <MemoryRouter initialEntries={[{ pathname: path, state }]}>
      <ToastProvider>
        <BusinessProvider>
          <App retryDelaysMs={[0]} />
          <CurrentLocation />
        </BusinessProvider>
      </ToastProvider>
    </MemoryRouter>,
  )
}

/** Reset module-level caches between tests. */
export function resetAppState() {
  discardPrefetchedReviews()
  clearReplyDrafts()
  resetBusinessSwitcherCache()
  localStorage.clear()
}
