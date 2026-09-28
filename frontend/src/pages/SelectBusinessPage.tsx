import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Building2, Check, MapPin, Search } from 'lucide-react'
import { ApiError, api } from '../lib/api'
import type { LocationSummary } from '../lib/types'
import { useBusiness } from '../context/BusinessContext'
import { useToast } from '../context/ToastContext'
import { Avatar } from '../components/Avatar'
import { BusinessCardSkeleton } from '../components/Skeletons'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { classNames } from '../lib/utils'

export function SelectBusinessPage({ onAuthExpired }: { onAuthExpired: () => void }) {
  const { business, selectBusiness } = useBusiness()
  const toast = useToast()
  const navigate = useNavigate()

  const [locations, setLocations] = useState<LocationSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(business?.location_id ?? null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.listLocations()
      setLocations(data)
      setSelectedId((current) => current ?? data[0]?.location_id ?? null)
    } catch (err) {
      if (err instanceof ApiError && err.isAuth) {
        onAuthExpired()
        navigate('/login')
        return
      }
      setError(
        err instanceof ApiError ? err.message : 'Could not load your businesses.',
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return locations
    return locations.filter(
      (l) =>
        l.name.toLowerCase().includes(q) || l.address.toLowerCase().includes(q),
    )
  }, [locations, query])

  const handleContinue = () => {
    const chosen = locations.find((l) => l.location_id === selectedId)
    if (!chosen) {
      toast.info('Please select a business to continue.')
      return
    }
    selectBusiness(chosen)
    navigate('/dashboard')
  }

  return (
    <div className="min-h-screen bg-[#f6f8fb] px-4 py-10">
      <div className="mx-auto w-full max-w-xl">
        <div className="mb-6 flex items-center justify-center gap-2 text-sm font-semibold text-slate-500">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600">
            <Building2 className="h-4 w-4 text-white" />
          </div>
          Review Reply AI
        </div>

        <div className="card p-6 sm:p-8">
          <h1 className="text-xl font-bold text-slate-900">Select Your Business Location</h1>
          <p className="mt-1 text-sm text-slate-500">
            Choose the Google Business Profile you want to manage.
          </p>

          <div className="relative mt-5">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              className="input pl-10"
              placeholder="Search business name or location…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              disabled={loading || !!error}
            />
          </div>

          <div className="mt-4 space-y-3">
            {loading ? (
              <>
                <BusinessCardSkeleton />
                <BusinessCardSkeleton />
                <BusinessCardSkeleton />
              </>
            ) : error ? (
              <ErrorState message={error} onRetry={load} />
            ) : locations.length === 0 ? (
              <EmptyState
                icon={Building2}
                title="No businesses found"
                description="This Google account doesn’t manage any Business Profiles, or the Business Profile APIs aren’t enabled yet."
              />
            ) : filtered.length === 0 ? (
              <EmptyState
                icon={Search}
                title="No matches"
                description="No business matches your search."
              />
            ) : (
              filtered.map((location) => {
                const active = location.location_id === selectedId
                return (
                  <button
                    key={location.location_id}
                    onClick={() => setSelectedId(location.location_id)}
                    className={classNames(
                      'flex w-full items-center gap-3 rounded-xl border p-4 text-left transition-all',
                      active
                        ? 'border-brand-500 bg-brand-50/50 ring-1 ring-brand-500'
                        : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50',
                    )}
                  >
                    <span
                      className={classNames(
                        'flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2',
                        active ? 'border-brand-600 bg-brand-600' : 'border-slate-300',
                      )}
                    >
                      {active && <Check className="h-3 w-3 text-white" />}
                    </span>
                    <Avatar name={location.name} size="md" />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <h3 className="truncate text-sm font-semibold text-slate-900">
                          {location.name}
                        </h3>
                        {active && (
                          <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-600 ring-1 ring-inset ring-emerald-100">
                            Active
                          </span>
                        )}
                      </div>
                      {location.address && (
                        <p className="mt-0.5 flex items-center gap-1 truncate text-xs text-slate-500">
                          <MapPin className="h-3 w-3 shrink-0" />
                          <span className="truncate">{location.address}</span>
                        </p>
                      )}
                      <p className="mt-1 text-xs text-slate-500">
                        {location.total_reviews} reviews · {location.unanswered} unanswered
                      </p>
                    </div>
                  </button>
                )
              })
            )}
          </div>

          <div className="mt-6 flex justify-end">
            <button
              className="btn-primary"
              onClick={handleContinue}
              disabled={loading || !!error || !selectedId}
            >
              Continue
              <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
