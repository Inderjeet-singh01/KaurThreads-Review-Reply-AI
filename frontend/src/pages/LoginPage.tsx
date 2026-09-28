import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { MessagesSquare, ShieldCheck, Sparkles, ThumbsUp, Timer } from 'lucide-react'
import { ApiError, api } from '../lib/api'
import { useToast } from '../context/ToastContext'

const FEATURES = [
  {
    icon: Timer,
    title: 'Save Time',
    description: 'AI-powered reply suggestions',
    color: 'bg-violet-50 text-violet-500',
  },
  {
    icon: ThumbsUp,
    title: 'Professional',
    description: 'Polite & brand aligned replies',
    color: 'bg-emerald-50 text-emerald-500',
  },
  {
    icon: Sparkles,
    title: 'Better Reputation',
    description: 'Respond to reviews faster',
    color: 'bg-rose-50 text-rose-500',
  },
]

export function LoginPage({ onAuthenticated }: { onAuthenticated: () => void }) {
  const toast = useToast()
  const navigate = useNavigate()
  const [connecting, setConnecting] = useState(false)
  const pollRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current)
    }
  }, [])

  const startPolling = () => {
    if (pollRef.current) window.clearInterval(pollRef.current)
    let elapsed = 0
    pollRef.current = window.setInterval(async () => {
      elapsed += 2
      try {
        const status = await api.getAuthStatus()
        if (status.authenticated) {
          if (pollRef.current) window.clearInterval(pollRef.current)
          setConnecting(false)
          toast.success('Connected to Google Business Profile.')
          onAuthenticated()
          navigate('/select-business')
        }
      } catch {
        /* keep polling */
      }
      if (elapsed >= 180 && pollRef.current) {
        window.clearInterval(pollRef.current)
        setConnecting(false)
      }
    }, 2000)
  }

  const handleSignIn = async () => {
    setConnecting(true)
    try {
      const { authorization_url, warning } = await api.getAuthorizeUrl()
      if (warning) toast.info(warning)
      window.open(authorization_url, '_blank', 'noopener,noreferrer')
      startPolling()
    } catch (err) {
      setConnecting(false)
      toast.error(
        err instanceof ApiError
          ? err.message
          : 'Could not start Google sign-in. Please try again.',
      )
    }
  }

  const handleAlreadyConnected = async () => {
    try {
      const status = await api.getAuthStatus()
      if (status.authenticated) {
        onAuthenticated()
        navigate('/select-business')
      } else {
        toast.info('Not connected yet. Finish the Google sign-in in the other tab.')
      }
    } catch {
      toast.error('Could not verify the connection. Please try again.')
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#f6f8fb] px-4 py-10">
      <div className="w-full max-w-md">
        <div className="card p-8 sm:p-10">
          {/* Google G mark */}
          <div className="flex justify-center">
            <GoogleG className="h-11 w-11" />
          </div>

          <h1 className="mt-5 text-center text-2xl font-bold text-slate-900">
            Manage Your Google Reviews with AI
          </h1>
          <p className="mt-2.5 text-center text-sm leading-relaxed text-slate-500">
            Connect your Google Business Profile to view and reply to customer reviews
            using AI.
          </p>

          <div className="mt-8 grid grid-cols-3 gap-3">
            {FEATURES.map(({ icon: Icon, title, description, color }) => (
              <div key={title} className="text-center">
                <div
                  className={`mx-auto flex h-11 w-11 items-center justify-center rounded-full ${color}`}
                >
                  <Icon className="h-5 w-5" />
                </div>
                <p className="mt-2 text-xs font-semibold text-slate-800">{title}</p>
                <p className="mt-0.5 text-[11px] leading-tight text-slate-400">
                  {description}
                </p>
              </div>
            ))}
          </div>

          <button
            onClick={handleSignIn}
            disabled={connecting}
            className="btn-primary mt-8 w-full py-3 text-[15px]"
          >
            {connecting ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                Waiting for Google…
              </>
            ) : (
              <>
                <GoogleG className="h-5 w-5 rounded-full bg-white p-0.5" />
                Sign in with Google
              </>
            )}
          </button>

          {connecting && (
            <button
              onClick={handleAlreadyConnected}
              className="mt-3 w-full text-center text-sm font-medium text-brand-600 hover:text-brand-700"
            >
              I’ve finished connecting
            </button>
          )}

          <p className="mt-6 flex items-center justify-center gap-1.5 text-center text-xs text-slate-400">
            <ShieldCheck className="h-3.5 w-3.5" />
            Your data is secure. We only access your Google Business Profile to manage
            reviews.
          </p>
        </div>

        <div className="mt-6 flex items-center justify-center gap-2 text-sm text-slate-400">
          <MessagesSquare className="h-4 w-4" />
          Review Reply AI
        </div>
      </div>
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
