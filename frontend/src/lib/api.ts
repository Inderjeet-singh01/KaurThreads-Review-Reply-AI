// Centralized API service layer. Every network call to the backend goes
// through here so components never touch fetch directly. Errors are normalized
// into ApiError with human-readable messages (raw backend/stack details are
// never surfaced to users).

import type {
  AuthStatus,
  AuthorizeResponse,
  AutomationStatus,
  BackfillJob,
  BackfillStartResponse,
  GenerateReplyResponse,
  LocationSummary,
  PublishResult,
  ReplyValidationResult,
  Review,
  ReviewStats,
} from './types'

const BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
).replace(/\/$/, '')

export class ApiError extends Error {
  status: number
  /** True when the failure is an authentication/session problem. */
  isAuth: boolean
  /** The backend's own user-safe `detail` text, when it sent one. */
  detail: string
  /** The parsed JSON error body (e.g. a 409 naming the running backfill job). */
  body: unknown

  constructor(message: string, status: number, detail = '', body: unknown = undefined) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.isAuth = status === 401
    this.detail = detail
    this.body = body
  }
}

/** Turn any backend/network failure into a safe, human-readable message. */
function humanMessage(status: number, detail: string): string {
  // Prefer a concise backend detail when it is genuinely readable; otherwise
  // fall back to a friendly message keyed off the status code.
  const friendlyByStatus: Record<number, string> = {
    401: 'Your Google session has expired. Please reconnect your account.',
    403: 'Google denied access. Check that this account can manage the business profile and that the Business Profile APIs are enabled.',
    404: 'We couldn’t find that item. It may have been removed.',
    409: 'This review has already been answered on Google.',
    429: 'Google is rate-limiting requests right now. Please wait a moment and try again.',
    502: 'We couldn’t reach Google right now. Please try again shortly.',
    503: 'The service is temporarily unavailable. Please try again shortly.',
  }

  if (status === 0) {
    return 'Cannot reach the server. Check your connection and that the backend is running.'
  }
  // Backend details for these are safe and specific enough to show.
  if ((status === 409 || status === 400) && detail) return detail
  return friendlyByStatus[status] || detail || 'Something went wrong. Please try again.'
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...options,
      // Content-Type only when there is a body: on a GET it would turn every
      // cross-origin call into a "non-simple" request, costing an extra CORS
      // preflight (OPTIONS) round trip before the real request.
      headers: options.body
        ? { 'Content-Type': 'application/json', ...options.headers }
        : options.headers,
    })
  } catch {
    // Network error / server down / CORS failure.
    throw new ApiError(humanMessage(0, ''), 0)
  }

  if (!response.ok) {
    let detail = ''
    let body: unknown
    try {
      body = await response.json()
      const b = body as { detail?: unknown; reason?: unknown } | null
      // FastAPI errors carry `detail`; backfill rejections carry `reason`.
      detail =
        typeof b?.detail === 'string' ? b.detail : typeof b?.reason === 'string' ? b.reason : ''
    } catch {
      detail = ''
    }
    throw new ApiError(humanMessage(response.status, detail), response.status, detail, body)
  }

  if (response.status === 204) return undefined as T
  try {
    return (await response.json()) as T
  } catch {
    return undefined as T
  }
}

function withLocation(path: string, locationId?: string | null): string {
  if (!locationId) return path
  const sep = path.includes('?') ? '&' : '?'
  return `${path}${sep}location_id=${encodeURIComponent(locationId)}`
}

// Concurrent status checks (React StrictMode's double effect in development,
// the login page polling while the app checks) share one request. Nothing is
// cached: every check after the previous one finished asks the server again.
let authStatusInFlight: Promise<AuthStatus> | null = null

// Longer than a Render free instance takes to wake, so a sleeping backend is
// waited for, but a request lost in the network cannot block every later
// check (they share it) forever. A timeout surfaces as a retryable error.
export const AUTH_STATUS_TIMEOUT_MS = 75_000

function getAuthStatusShared(): Promise<AuthStatus> {
  if (!authStatusInFlight) {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), AUTH_STATUS_TIMEOUT_MS)
    authStatusInFlight = request<AuthStatus>('/auth/google/status', {
      signal: controller.signal,
    }).finally(() => {
      clearTimeout(timer)
      authStatusInFlight = null
    })
  }
  return authStatusInFlight
}

export const api = {
  // --- Auth ---------------------------------------------------------------
  getAuthStatus: getAuthStatusShared,
  getAuthorizeUrl: () => request<AuthorizeResponse>('/auth/google/authorize'),
  logout: () => request<{ authenticated: boolean; message: string }>(
    '/auth/google/logout',
    { method: 'POST' },
  ),

  // --- Automation --------------------------------------------------------
  getAutomationStatus: () => request<AutomationStatus>('/automation/status'),

  /** Start replying to every pending review (runs in the background on the server). */
  startBackfill: (locationId?: string | null) =>
    request<BackfillStartResponse>(withLocation('/automation/backfill', locationId), {
      method: 'POST',
    }),
  getBackfill: (jobId: string) =>
    request<BackfillJob>(`/automation/backfill/${encodeURIComponent(jobId)}`),
  getLatestBackfill: (locationId?: string | null) =>
    request<{ job: BackfillJob | null }>(withLocation('/automation/backfill', locationId)),
  cancelBackfill: (jobId: string) =>
    request<BackfillJob>(`/automation/backfill/${encodeURIComponent(jobId)}/cancel`, {
      method: 'POST',
    }),

  // --- Locations ----------------------------------------------------------
  listLocations: () => request<LocationSummary[]>('/locations'),

  // --- Reviews ------------------------------------------------------------
  listAllReviews: (locationId?: string | null) =>
    request<Review[]>(withLocation('/reviews/all', locationId)),
  listUnansweredReviews: (locationId?: string | null) =>
    request<Review[]>(withLocation('/reviews', locationId)),
  getStats: (locationId?: string | null) =>
    request<ReviewStats>(withLocation('/reviews/stats', locationId)),

  generateReply: (
    reviewId: string,
    opts: { locationId?: string | null; tone?: string; length?: string } = {},
  ) => {
    const params = new URLSearchParams()
    if (opts.locationId) params.set('location_id', opts.locationId)
    if (opts.tone) params.set('tone', opts.tone)
    if (opts.length) params.set('length', opts.length)
    const qs = params.toString()
    return request<GenerateReplyResponse>(
      `/reviews/${encodeURIComponent(reviewId)}/generate${qs ? `?${qs}` : ''}`,
      { method: 'POST' },
    )
  },

  validateReply: (
    reviewId: string,
    reply: string,
    locationId?: string | null,
  ) =>
    request<ReplyValidationResult>(
      withLocation(`/reviews/${encodeURIComponent(reviewId)}/validate`, locationId),
      { method: 'POST', body: JSON.stringify({ reply }) },
    ),

  publishReply: (
    reviewId: string,
    reply: string,
    locationId?: string | null,
  ) =>
    request<PublishResult>(
      withLocation(`/reviews/${encodeURIComponent(reviewId)}/publish`, locationId),
      { method: 'POST', body: JSON.stringify({ reply }) },
    ),
}

// --- Startup prefetch ------------------------------------------------------
// The app shell needs /auth/google/status before it may show anything
// protected. The review list is independent of that answer on the server
// (the reviews endpoint checks the credentials itself), so App starts it in
// parallel and ReviewsProvider picks it up — only after authentication was
// confirmed, so nothing is displayed earlier. Unused or stale prefetches are
// dropped.
const PREFETCH_MAX_AGE_MS = 30_000

let reviewsPrefetch: { locationId: string | null; at: number; promise: Promise<Review[]> } | null =
  null

export function prefetchAllReviews(locationId: string | null): void {
  if (
    reviewsPrefetch &&
    reviewsPrefetch.locationId === locationId &&
    Date.now() - reviewsPrefetch.at < PREFETCH_MAX_AGE_MS
  ) {
    return // already running (e.g. StrictMode's second effect run)
  }
  const promise = api.listAllReviews(locationId)
  promise.catch(() => undefined) // consumed (or retried) by ReviewsProvider
  reviewsPrefetch = { locationId, at: Date.now(), promise }
}

/** The prefetched list for this location, at most once; null when none. */
export function takePrefetchedReviews(locationId: string | null): Promise<Review[]> | null {
  const prefetch = reviewsPrefetch
  reviewsPrefetch = null
  if (
    !prefetch ||
    prefetch.locationId !== locationId ||
    Date.now() - prefetch.at >= PREFETCH_MAX_AGE_MS
  ) {
    return null
  }
  return prefetch.promise
}

export function discardPrefetchedReviews(): void {
  reviewsPrefetch = null
}
