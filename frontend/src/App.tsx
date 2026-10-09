import { useCallback, useEffect, useRef, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { discardPrefetchedReviews, prefetchAllReviews } from './lib/api'
import { AUTH_RETRY_DELAYS_MS, checkAuth } from './lib/authCheck'
import {
  CONNECTED_MESSAGE,
  NOT_VERIFIED_MESSAGE,
  parseGoogleAuthReturn,
  withoutGoogleAuthParams,
} from './lib/googleAuth'
import { useBusiness } from './context/BusinessContext'
import { useToast } from './context/ToastContext'
import { ReviewsProvider } from './context/ReviewsContext'
import { SessionContext } from './context/SessionContext'
import { AppShell } from './components/AppShell'
import { StartupScreen } from './components/StartupScreen'
import { LoginPage } from './pages/LoginPage'
import { SelectBusinessPage } from './pages/SelectBusinessPage'
import { OverviewPage } from './pages/OverviewPage'
import { ReviewInboxPage } from './pages/ReviewInboxPage'
import { AutomationPage } from './pages/AutomationPage'
import { RepliedReviewsPage } from './pages/RepliedReviewsPage'
import { AnalyticsPage } from './pages/AnalyticsPage'
import { SettingsPage } from './pages/SettingsPage'

type AuthState = 'checking' | 'authed' | 'unauthed' | 'unavailable'

// Routes that never show the review list: no review prefetch for them.
const NO_PREFETCH_PATHS = ['/login', '/select-business']

export default function App({ retryDelaysMs = AUTH_RETRY_DELAYS_MS }: { retryDelaysMs?: number[] }) {
  const [auth, setAuth] = useState<AuthState>('checking')
  const [unavailableMessage, setUnavailableMessage] = useState('')
  const [checkId, setCheckId] = useState(0)
  const [loginError, setLoginError] = useState<string | null>(null)
  const { business } = useBusiness()
  const { pathname, search, hash } = useLocation()
  const navigate = useNavigate()
  const toast = useToast()
  // Set when this page load is the return from Google sign-in (read once).
  const [googleAuthReturn] = useState(() => parseGoogleAuthReturn(search))
  const googleAuthReturnHandled = useRef(false)

  useEffect(() => {
    // Drop the one-time sign-in parameters from the address bar.
    if (googleAuthReturn) {
      navigate({ pathname, search: withoutGoogleAuthParams(search), hash }, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    let cancelled = false
    setAuth('checking')
    // The review list does not depend on the status answer on the server, so
    // fetch it in parallel; it is only shown once authentication is confirmed.
    if (checkId === 0 && business && !NO_PREFETCH_PATHS.includes(pathname)) {
      prefetchAllReviews(business.location_id)
    }
    void checkAuth(retryDelaysMs, () => cancelled).then((result) => {
      if (cancelled) return
      if (result.state !== 'authed') discardPrefetchedReviews()
      if (result.state === 'unavailable') setUnavailableMessage(result.message)
      // Report a sign-in return once the server has answered; the URL never
      // decides on its own whether Google is connected.
      if (googleAuthReturn && result.state !== 'unavailable' && !googleAuthReturnHandled.current) {
        googleAuthReturnHandled.current = true
        const authed = result.state === 'authed'
        if (googleAuthReturn.outcome === 'success' && authed) toast.success(CONNECTED_MESSAGE)
        else {
          const message =
            googleAuthReturn.outcome === 'error' ? googleAuthReturn.message : NOT_VERIFIED_MESSAGE
          if (authed) toast.error(message)
          else setLoginError(message)
        }
      }
      setAuth(result.state)
    })
    return () => {
      cancelled = true
    }
    // Runs on mount and on "Try again" only, not on navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkId])

  const authed = auth === 'authed'
  const onAuthExpired = useCallback(() => setAuth('unauthed'), [])

  if (auth === 'checking') return <StartupScreen />
  if (auth === 'unavailable') {
    return <StartupScreen error={unavailableMessage} onRetry={() => setCheckId((id) => id + 1)} />
  }

  return (
    <Routes>
      <Route
        path="/login"
        element={
          authed ? (
            <Navigate to="/select-business" replace />
          ) : (
            <LoginPage error={loginError} />
          )
        }
      />
      <Route
        path="/select-business"
        element={
          authed ? (
            <SelectBusinessPage onAuthExpired={onAuthExpired} />
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />

      <Route element={<ProtectedShell authed={authed} onAuthExpired={onAuthExpired} />}>
        <Route path="/dashboard" element={<OverviewPage onAuthExpired={onAuthExpired} />} />
        {/* /reviews/:reviewId keeps direct links to a review working. */}
        <Route path="/reviews/:reviewId?" element={<ReviewInboxPage onAuthExpired={onAuthExpired} />} />
        <Route path="/replied" element={<RepliedReviewsPage onAuthExpired={onAuthExpired} />} />
        <Route path="/analytics" element={<AnalyticsPage onAuthExpired={onAuthExpired} />} />
        <Route path="/automation" element={<AutomationPage onAuthExpired={onAuthExpired} />} />
        <Route path="/settings" element={<SettingsPage onAuthExpired={onAuthExpired} />} />
      </Route>

      <Route path="*" element={<Navigate to={authed ? '/dashboard' : '/login'} replace />} />
    </Routes>
  )
}

function ProtectedShell({ authed, onAuthExpired }: { authed: boolean; onAuthExpired: () => void }) {
  const { business } = useBusiness()
  if (!authed) return <Navigate to="/login" replace />
  if (!business) return <Navigate to="/select-business" replace />
  return (
    <SessionContext.Provider value={{ onAuthExpired }}>
      <ReviewsProvider>
        <AppShell />
      </ReviewsProvider>
    </SessionContext.Provider>
  )
}
