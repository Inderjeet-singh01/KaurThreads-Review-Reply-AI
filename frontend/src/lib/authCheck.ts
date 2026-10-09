// Startup authentication check. Distinguishes "not connected" (show the login
// page) from "could not check right now" (backend waking up, deploying, its
// database unreachable): the latter is retried and never signs the user out.

import { ApiError, api } from './api'
import type { AuthStatus } from './types'

export type AuthCheckResult =
  | { state: 'authed' }
  | { state: 'unauthed' }
  | { state: 'unavailable'; message: string }

/** Automatic retries after a transient failure (then a manual "Try again"). */
export const AUTH_RETRY_DELAYS_MS = [1500, 3000, 6000]

const UNAVAILABLE_MESSAGE =
  'The server is temporarily unavailable. Your Google connection has not changed.'

export function interpretAuthStatus(status: AuthStatus): AuthCheckResult {
  if (status.authenticated) return { state: 'authed' }
  // The server could not check (credential database or Google unavailable).
  if (status.retryable) return { state: 'unavailable', message: UNAVAILABLE_MESSAGE }
  return { state: 'unauthed' }
}

export function interpretAuthError(err: unknown): AuthCheckResult {
  if (err instanceof ApiError && err.status === 401) return { state: 'unauthed' }
  // Network error, 5xx while Render restarts/deploys, anything unexpected.
  const message =
    err instanceof ApiError && err.status === 0
      ? 'Cannot reach the server right now. Check your connection — your Google connection has not changed.'
      : UNAVAILABLE_MESSAGE
  return { state: 'unavailable', message }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * Ask the server whether Google is connected, retrying transient failures.
 * `isCancelled` stops the retry loop when the caller unmounts.
 */
export async function checkAuth(
  retryDelaysMs: number[] = AUTH_RETRY_DELAYS_MS,
  isCancelled: () => boolean = () => false,
): Promise<AuthCheckResult> {
  let result: AuthCheckResult = { state: 'unavailable', message: UNAVAILABLE_MESSAGE }
  for (let attempt = 0; attempt <= retryDelaysMs.length; attempt++) {
    if (attempt > 0) {
      await sleep(retryDelaysMs[attempt - 1])
      if (isCancelled()) return result
    }
    try {
      result = interpretAuthStatus(await api.getAuthStatus())
    } catch (err) {
      result = interpretAuthError(err)
    }
    if (result.state !== 'unavailable') return result
  }
  return result
}
