import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  AUTH_STATUS_TIMEOUT_MS,
  ApiError,
  api,
  discardPrefetchedReviews,
  prefetchAllReviews,
  takePrefetchedReviews,
} from './api'
import { checkAuth, interpretAuthError, interpretAuthStatus } from './authCheck'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn(async () => json({ authenticated: true }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  discardPrefetchedReviews()
})

describe('request headers', () => {
  it('GET requests are CORS-simple (no Content-Type)', async () => {
    await api.listAllReviews('loc1')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).has('Content-Type')).toBe(false)
  })

  it('requests with a JSON body keep Content-Type', async () => {
    await api.validateReply('r1', 'Thanks!', 'loc1')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
  })
})

describe('getAuthStatus', () => {
  it('shares one request between concurrent callers, and never caches', async () => {
    const [a, b] = await Promise.all([api.getAuthStatus(), api.getAuthStatus()])
    expect(a).toEqual(b)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await api.getAuthStatus()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('times out a request that never answers, so later checks are not stuck', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementationOnce(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) =>
          init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))),
        ),
    )
    const hung = api.getAuthStatus()
    vi.advanceTimersByTime(AUTH_STATUS_TIMEOUT_MS)
    await expect(hung).rejects.toMatchObject({ status: 0 })
    vi.useRealTimers()
    await expect(api.getAuthStatus()).resolves.toEqual({ authenticated: true })
  })

  it('a failed check is not reused', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await expect(api.getAuthStatus()).rejects.toBeInstanceOf(ApiError)
    await expect(api.getAuthStatus()).resolves.toEqual({ authenticated: true })
  })
})

describe('review prefetch', () => {
  it('is taken once, and only for the same location', async () => {
    fetchMock.mockImplementation(async () => json([]))
    prefetchAllReviews('loc1')
    prefetchAllReviews('loc1') // duplicate start is ignored
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(takePrefetchedReviews('other')).toBeNull()
    prefetchAllReviews('loc1')
    expect(await takePrefetchedReviews('loc1')).toEqual([])
    expect(takePrefetchedReviews('loc1')).toBeNull()
  })

  it('can be discarded', () => {
    prefetchAllReviews('loc1')
    discardPrefetchedReviews()
    expect(takePrefetchedReviews('loc1')).toBeNull()
  })
})

describe('auth check', () => {
  it('interprets the status payload', () => {
    expect(interpretAuthStatus({ authenticated: true }).state).toBe('authed')
    expect(interpretAuthStatus({ authenticated: false }).state).toBe('unauthed')
    expect(interpretAuthStatus({ authenticated: false, retryable: true }).state).toBe('unavailable')
  })

  it('only a 401 means signed out', () => {
    expect(interpretAuthError(new ApiError('x', 401)).state).toBe('unauthed')
    for (const status of [0, 404, 500, 502, 503]) {
      expect(interpretAuthError(new ApiError('x', status)).state).toBe('unavailable')
    }
    expect(interpretAuthError(new Error('boom')).state).toBe('unavailable')
  })

  it('retries transient failures, then gives up', async () => {
    fetchMock.mockImplementation(async () => json({ detail: 'down' }, 503))
    expect((await checkAuth([0, 0])).state).toBe('unavailable')
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('stops retrying when cancelled', async () => {
    fetchMock.mockImplementation(async () => json({ detail: 'down' }, 503))
    await checkAuth([0, 0], () => true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
