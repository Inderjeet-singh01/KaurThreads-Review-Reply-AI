import { useEffect, useId, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Building2, Check, ChevronDown, Loader2, MapPin } from 'lucide-react'
import { ApiError, api } from '../lib/api'
import type { LocationSummary } from '../lib/types'
import { useBusiness } from '../context/BusinessContext'
import { classNames } from '../lib/utils'
import { Avatar } from './Avatar'

// The location list rarely changes; load it once per session, on first open.
let locationsRequest: Promise<LocationSummary[]> | null = null

function loadLocations(): Promise<LocationSummary[]> {
  if (!locationsRequest) {
    locationsRequest = api.listLocations().catch((err) => {
      locationsRequest = null // allow a retry
      throw err
    })
  }
  return locationsRequest
}

/** Forget the cached list (after signing out). */
// eslint-disable-next-line react-refresh/only-export-components
export function resetBusinessSwitcherCache() {
  locationsRequest = null
}

/** Current business with a menu to switch to another Business Profile location. */
export function BusinessSwitcher({ onAuthExpired }: { onAuthExpired?: () => void }) {
  const { business, selectBusiness } = useBusiness()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [open, setOpen] = useState(false)
  const [locations, setLocations] = useState<LocationSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)
  const menuId = useId()

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setError(null)
    loadLocations()
      .then((data) => !cancelled && setLocations(data))
      .catch((err) => {
        if (cancelled) return
        if (err instanceof ApiError && err.isAuth) {
          setOpen(false)
          onAuthExpired?.()
          return
        }
        setError(err instanceof ApiError ? err.message : 'Could not load your businesses.')
      })
    function onPointer(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setOpen(false)
        rootRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
      }
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      cancelled = true
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, attempt, onAuthExpired])

  const choose = (location: LocationSummary) => {
    setOpen(false)
    if (location.location_id === business?.location_id) return
    selectBusiness(location)
    // A review of the previous business cannot be shown for the new one.
    if (pathname.startsWith('/reviews/')) navigate('/reviews')
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={business ? `Business: ${business.name}. Switch business` : 'Select a business'}
        className="focus-ring flex w-full items-center gap-2.5 rounded-xl border border-line bg-white py-1.5 pl-1.5 pr-3 text-left shadow-card transition-colors hover:bg-slate-50 sm:w-auto sm:max-w-xs"
      >
        {business ? (
          <Avatar name={business.name} size="sm" square />
        ) : (
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100">
            <Building2 className="h-4 w-4 text-slate-500" />
          </span>
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-semibold text-ink">
            {business?.name ?? 'Select a business'}
          </span>
          {business?.address && (
            <span className="block truncate text-xs text-ink-muted">{business.address}</span>
          )}
        </span>
        <ChevronDown
          className={classNames('h-4 w-4 shrink-0 text-slate-400 transition-transform', open && 'rotate-180')}
          aria-hidden
        />
      </button>

      {open && (
        <div
          id={menuId}
          className="absolute right-0 z-40 mt-2 w-full min-w-[18rem] animate-fade-in overflow-hidden rounded-xl border border-line bg-white shadow-overlay sm:w-80"
        >
          <p className="border-b border-line px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-ink-muted">
            Your businesses
          </p>
          <div className="scroll-area max-h-72 overflow-y-auto p-1.5">
            {error ? (
              <div className="px-3 py-4 text-sm">
                <p className="text-red-700">{error}</p>
                <button
                  className="mt-2 font-semibold text-brand-600 hover:text-brand-700"
                  onClick={() => setAttempt((n) => n + 1)}
                >
                  Try again
                </button>
              </div>
            ) : !locations ? (
              <p className="flex items-center gap-2 px-3 py-4 text-sm text-ink-muted">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading businesses…
              </p>
            ) : locations.length === 0 ? (
              <p className="px-3 py-4 text-sm text-ink-muted">No Business Profile locations found.</p>
            ) : (
              <ul>
                {locations.map((location) => {
                  const current = location.location_id === business?.location_id
                  return (
                    <li key={location.location_id}>
                      <button
                        type="button"
                        onClick={() => choose(location)}
                        aria-current={current ? 'true' : undefined}
                        className={classNames(
                          'focus-ring flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left transition-colors',
                          current ? 'bg-brand-50' : 'hover:bg-slate-50',
                        )}
                      >
                        <Avatar name={location.name} size="sm" square />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-ink">
                            {location.name}
                          </span>
                          {location.address && (
                            <span className="flex items-center gap-1 truncate text-xs text-ink-muted">
                              <MapPin className="h-3 w-3 shrink-0" aria-hidden />
                              <span className="truncate">{location.address}</span>
                            </span>
                          )}
                        </span>
                        {current && <Check className="h-4 w-4 shrink-0 text-brand-600" aria-hidden />}
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
          <div className="border-t border-line p-1.5">
            <button
              type="button"
              onClick={() => {
                setOpen(false)
                navigate('/select-business')
              }}
              className="focus-ring w-full rounded-lg px-3 py-2 text-left text-sm font-medium text-brand-700 hover:bg-brand-50"
            >
              Manage businesses
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
