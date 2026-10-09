import { useState } from 'react'
import { Lock, ShieldCheck, Timer, TrendingUp } from 'lucide-react'
import { ApiError, api } from '../lib/api'
import { goToGoogleSignIn } from '../lib/googleAuth'
import { useToast } from '../context/ToastContext'
import { BrandMark } from '../components/BrandMark'

const BENEFITS = [
  {
    icon: Timer,
    title: 'Reply in seconds',
    description: 'AI drafts a thoughtful reply for every review.',
  },
  {
    icon: ShieldCheck,
    title: 'You stay in control',
    description: 'Edit and check each reply, then publish it when you are happy.',
  },
  {
    icon: TrendingUp,
    title: 'Grow your reputation',
    description: 'Answer every customer and track ratings over time.',
  },
]

/** `error`: why the last Google sign-in did not connect, shown until retried. */
export function LoginPage({ error }: { error?: string | null }) {
  const toast = useToast()
  const [connecting, setConnecting] = useState(false)

  // The page is left for Google in this tab; the backend's OAuth callback
  // brings the browser back to the app, which then checks the connection.
  const handleSignIn = async () => {
    setConnecting(true)
    try {
      const { authorization_url, warning } = await api.getAuthorizeUrl()
      if (warning) toast.info(warning)
      goToGoogleSignIn(authorization_url)
    } catch (err) {
      setConnecting(false)
      toast.error(
        err instanceof ApiError
          ? err.message
          : 'Could not start Google sign-in. Please try again.',
      )
    }
  }

  return (
    <div className="flex min-h-screen bg-canvas">
      {/* Brand panel (large screens) */}
      <aside className="relative hidden w-[44%] max-w-xl flex-col justify-between overflow-hidden bg-navy-900 p-12 text-white lg:flex">
        <div
          className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-brand-600/20 blur-3xl"
          aria-hidden
        />
        <div className="relative flex items-center gap-2.5">
          <BrandMark className="h-9 w-9" />
          <span className="text-lg font-semibold">Review Reply AI</span>
        </div>
        <div className="relative">
          <p className="text-3xl font-bold leading-tight tracking-tight">
            Every Google review answered — thoughtfully and on time.
          </p>
          <ul className="mt-10 space-y-6">
            {BENEFITS.map(({ icon: Icon, title, description }) => (
              <li key={title} className="flex gap-4">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10 text-brand-300" aria-hidden>
                  <Icon className="h-5 w-5" />
                </span>
                <span>
                  <span className="block font-semibold">{title}</span>
                  <span className="block text-sm text-slate-400">{description}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-slate-500">Works with Google Business Profile</p>
      </aside>

      {/* Sign-in */}
      <main className="flex flex-1 items-center justify-center px-4 py-10 sm:px-8">
        <div className="w-full max-w-md">
          <div className="card p-7 sm:p-10">
            <BrandMark className="mx-auto h-12 w-12 lg:hidden" />
            <p className="mt-4 text-center text-sm font-semibold text-brand-700 lg:mt-0">
              Review Reply AI
            </p>
            <h1 className="mt-2 text-center text-2xl font-bold tracking-tight text-ink">
              Manage Your Google Reviews with AI
            </h1>
            <p className="mt-2.5 text-center text-sm leading-relaxed text-ink-muted">
              Connect your Google Business Profile to read your reviews, draft replies with AI and
              publish them to Google.
            </p>

            {error && (
              <p
                role="alert"
                className="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
              >
                {error}
              </p>
            )}

            <button
              onClick={handleSignIn}
              disabled={connecting}
              className="focus-ring mt-6 flex w-full items-center justify-center gap-3 rounded-[10px] border border-line bg-white px-4 py-3 text-[15px] font-semibold text-ink shadow-sm transition-colors hover:bg-slate-50 disabled:cursor-wait disabled:opacity-70"
            >
              {connecting ? (
                <>
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" aria-hidden />
                  Redirecting to Google…
                </>
              ) : (
                <>
                  <GoogleG className="h-5 w-5" />
                  Sign in with Google
                </>
              )}
            </button>

            <ul className="mt-8 space-y-3 lg:hidden">
              {BENEFITS.map(({ icon: Icon, title, description }) => (
                <li key={title} className="flex gap-3 text-sm">
                  <Icon className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" aria-hidden />
                  <span>
                    <span className="font-semibold text-ink">{title}.</span>{' '}
                    <span className="text-ink-muted">{description}</span>
                  </span>
                </li>
              ))}
            </ul>

            <p className="mt-8 flex items-start justify-center gap-2 text-center text-xs leading-relaxed text-ink-muted">
              <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              Secure Google sign-in. Access is limited to managing your Business Profile; your
              Google credentials stay encrypted on our server.
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}

function GoogleG({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 48 48" aria-hidden>
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  )
}
