import { useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { api } from './lib/api'
import { useBusiness } from './context/BusinessContext'
import { ReviewsProvider } from './context/ReviewsContext'
import { AppShell } from './components/AppShell'
import { LoginPage } from './pages/LoginPage'
import { SelectBusinessPage } from './pages/SelectBusinessPage'
import { DashboardPage } from './pages/DashboardPage'
import { ReviewDetailPage } from './pages/ReviewDetailPage'
import { RepliedReviewsPage } from './pages/RepliedReviewsPage'
import { AnalyticsPage } from './pages/AnalyticsPage'
import { SettingsPage } from './pages/SettingsPage'

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .getAuthStatus()
      .then((status) => {
        if (!cancelled) setAuthed(status.authenticated)
      })
      .catch(() => {
        if (!cancelled) setAuthed(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const onAuthExpired = () => setAuthed(false)

  if (authed === null) return <FullPageLoader />

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
        <Route path="/dashboard" element={<DashboardPage defaultTab="unanswered" onAuthExpired={onAuthExpired} />} />
        <Route path="/reviews" element={<DashboardPage defaultTab="all" onAuthExpired={onAuthExpired} />} />
        <Route path="/reviews/:reviewId" element={<ReviewDetailPage onAuthExpired={onAuthExpired} />} />
        <Route path="/replied" element={<RepliedReviewsPage onAuthExpired={onAuthExpired} />} />
        <Route path="/analytics" element={<AnalyticsPage onAuthExpired={onAuthExpired} />} />
        <Route path="/settings" element={<SettingsPage onAuthExpired={onAuthExpired} />} />
      </Route>

      <Route path="*" element={<Navigate to={authed ? '/dashboard' : '/login'} replace />} />
    </Routes>
  )
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

function FullPageLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[#f6f8fb]">
      <div className="flex flex-col items-center gap-3">
        <span className="h-8 w-8 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" />
        <p className="text-sm font-medium text-slate-500">Loading…</p>
      </div>
    </div>
  )
}
