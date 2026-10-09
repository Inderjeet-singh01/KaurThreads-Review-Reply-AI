// Google sign-in round trip. The browser leaves for Google in the same tab;
// the backend's OAuth callback stores the credentials and then sends it back
// to the app with ?google_auth=success or ?google_auth=error&reason=<code>.
// Those parameters only choose which message to show — whether Google is
// connected is always decided by GET /auth/google/status.

const OUTCOME_PARAM = 'google_auth'
const REASON_PARAM = 'reason'

export type GoogleAuthReturn = { outcome: 'success' } | { outcome: 'error'; message: string }

export const CONNECTED_MESSAGE = 'Connected to Google Business Profile.'
export const NOT_VERIFIED_MESSAGE =
  'Google sign-in finished, but the connection could not be verified. Please sign in again.'

const DEFAULT_ERROR = 'Google sign-in could not be completed. Please try again.'

// Fixed backend reason codes; anything else gets the default message, so the
// URL can never put arbitrary text on screen.
const ERROR_MESSAGES: Record<string, string> = {
  access_denied: 'Google sign-in was cancelled. Nothing was changed.',
  state_mismatch: 'That Google sign-in attempt expired. Please sign in again.',
  no_refresh_token:
    'Google did not grant ongoing access. Remove this app at myaccount.google.com/permissions, then sign in again.',
  storage_unavailable:
    'Google sign-in worked, but the server could not save the connection. Please try again shortly.',
  not_configured: 'Google sign-in is not configured on the server.',
}

/** The sign-in result carried by the URL the callback redirected to, if any. */
export function parseGoogleAuthReturn(search: string): GoogleAuthReturn | null {
  const params = new URLSearchParams(search)
  const outcome = params.get(OUTCOME_PARAM)
  if (outcome === 'success') return { outcome: 'success' }
  if (outcome === 'error') {
    const reason = params.get(REASON_PARAM) ?? ''
    return {
      outcome: 'error',
      message: Object.prototype.hasOwnProperty.call(ERROR_MESSAGES, reason)
        ? ERROR_MESSAGES[reason]
        : DEFAULT_ERROR,
    }
  }
  return null
}

/** `search` without the sign-in parameters ('' when nothing else is left). */
export function withoutGoogleAuthParams(search: string): string {
  const params = new URLSearchParams(search)
  params.delete(OUTCOME_PARAM)
  params.delete(REASON_PARAM)
  const rest = params.toString()
  return rest ? `?${rest}` : ''
}

/** Leave the app for Google's sign-in page (a full navigation, same tab). */
export function goToGoogleSignIn(authorizationUrl: string): void {
  window.location.assign(authorizationUrl)
}
