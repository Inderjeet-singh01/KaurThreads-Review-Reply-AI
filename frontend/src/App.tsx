import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { discardPrefetchedReviews, prefetchAllReviews } from './lib/api'
import { AUTH_RETRY_DELAYS_MS, checkAuth } from './lib/authCheck'
import { useBusiness } from './context/BusinessContext'
import { ReviewsProvider } from './context/ReviewsContext'
import { AppShell } from './components/AppShell'
import { StartupScreen } from './components/StartupScreen'
import { LoginPage } from './pages/LoginPage'
import { SelectBusinessPage } from './pages/SelectBusinessPage'
import { DashboardPage } from './pages/DashboardPage'
import { ReviewDetailPage } from './pages/ReviewDetailPage'
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
  const { business } = useBusiness()
  const { pathname } = useLocation()

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
      setAuth(result.state)
    })
    return () => {
      cancelled = true
    }
    // Runs on mount and on "Try again" only, not on navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkId])

  const authed = auth === 'authed'
  const setAuthed = (value: boolean) => setAuth(value ? 'authed' : 'unauthed')
  const onAuthExpired = () => setAuth('unauthed')

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
            <LoginPage onAuthenticated={() => setAuthed(true)} />
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

      <Route element={<ProtectedShell authed={authed} />}>
        {/* Distinct keys: both routes render DashboardPage, and without them
            React would reuse one instance and keep the previous tab. */}
        <Route path="/dashboard" element={<DashboardPage key="dashboard" defaultTab="unanswered" onAuthExpired={onAuthExpired} />} />
        <Route path="/reviews" element={<DashboardPage key="reviews" defaultTab="all" onAuthExpired={onAuthExpired} />} />
        <Route path="/reviews/:reviewId" element={<ReviewDetailRoute onAuthExpired={onAuthExpired} />} />
        <Route path="/replied" element={<RepliedReviewsPage onAuthExpired={onAuthExpired} />} />
        <Route path="/analytics" element={<AnalyticsPage onAuthExpired={onAuthExpired} />} />
        <Route path="/settings" element={<SettingsPage onAuthExpired={onAuthExpired} />} />
      </Route>

      <Route path="*" element={<Navigate to={authed ? '/dashboard' : '/login'} replace />} />
    </Routes>
  )
}

/** Fresh detail state (draft, validation, auto-generation) per review id. */
function ReviewDetailRoute({ onAuthExpired }: { onAuthExpired: () => void }) {
  const { reviewId = '' } = useParams()
  return <ReviewDetailPage key={reviewId} onAuthExpired={onAuthExpired} />
}

function ProtectedShell({ authed }: { authed: boolean }) {
  const { business } = useBusiness()
  if (!authed) return <Navigate to="/login" replace />
  if (!business) return <Navigate to="/select-business" replace />
  return (
    <ReviewsProvider>
      <AppShell />
    </ReviewsProvider>
  )
}
