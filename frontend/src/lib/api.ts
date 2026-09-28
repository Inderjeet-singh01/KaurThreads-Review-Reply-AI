// Centralized API service layer. Every network call to the backend goes
// through here so components never touch fetch directly. Errors are normalized
// into ApiError with human-readable messages (raw backend/stack details are
// never surfaced to users).

import type {
  AuthStatus,
  AuthorizeResponse,
  GenerateReplyResponse,
  LocationSummary,
  PublishResult,
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

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.isAuth = status === 401
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
      headers: { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    })
  } catch {
    // Network error / server down / CORS failure.
    throw new ApiError(humanMessage(0, ''), 0)
  }

  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      detail = typeof body?.detail === 'string' ? body.detail : ''
    } catch {
      detail = ''
    }
    throw new ApiError(humanMessage(response.status, detail), response.status)
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

export const api = {
  // --- Auth ---------------------------------------------------------------
  getAuthStatus: () => request<AuthStatus>('/auth/google/status'),
  getAuthorizeUrl: () => request<AuthorizeResponse>('/auth/google/authorize'),
  logout: () => request<{ authenticated: boolean; message: string }>(
    '/auth/google/logout',
    { method: 'POST' },
  ),

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
