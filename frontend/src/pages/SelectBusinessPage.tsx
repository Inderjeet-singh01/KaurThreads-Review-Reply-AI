import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Building2, Check, MapPin, SearchX, Star } from 'lucide-react'
import { ApiError, api } from '../lib/api'
import type { LocationSummary } from '../lib/types'
import { useBusiness } from '../context/BusinessContext'
import { useToast } from '../context/ToastContext'
import { Avatar } from '../components/Avatar'
import { BrandMark } from '../components/BrandMark'
import { Badge } from '../components/StatusBadge'
import { SearchInput } from '../components/FormControls'
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
    <div className="min-h-screen bg-canvas px-4 py-10 sm:py-14">
      <div className="mx-auto w-full max-w-2xl">
        <div className="mb-8 flex items-center justify-center gap-2.5">
          <BrandMark className="h-8 w-8" />
          <span className="text-base font-semibold text-ink">Review Reply AI</span>
        </div>

        <div className="card overflow-hidden">
          <div className="p-6 sm:p-8">
            <h1 className="text-2xl font-bold tracking-tight text-ink">Select your business</h1>
            <p className="mt-1 text-sm text-ink-muted">
              Choose the Google Business Profile location you want to manage. You can switch at any
              time.
            </p>

            {(loading || locations.length > 3) && !error && (
              <SearchInput
                className="mt-5"
                label="Search businesses"
                placeholder="Search business name or address…"
                value={query}
                onChange={setQuery}
              />
            )}

            <div className="mt-4">
              {loading ? (
                <div className="space-y-3">
                  <BusinessCardSkeleton />
                  <BusinessCardSkeleton />
                  <BusinessCardSkeleton />
                </div>
              ) : error ? (
                <ErrorState message={error} onRetry={load} />
              ) : locations.length === 0 ? (
                <EmptyState
                  icon={Building2}
                  title="No businesses found"
                  description="This Google account doesn’t manage any Business Profiles, or the Business Profile APIs aren’t enabled yet."
                />
              ) : filtered.length === 0 ? (
                <EmptyState icon={SearchX} title="No matches" description="No business matches your search." />
              ) : (
                <div role="group" aria-label="Business locations" className="space-y-3">
                  {filtered.map((location) => {
                    const active = location.location_id === selectedId
                    return (
                      <button
                        key={location.location_id}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setSelectedId(location.location_id)}
                        className={classNames(
                          'focus-ring flex w-full items-center gap-4 rounded-xl border p-4 text-left transition-colors',
                          active
                            ? 'border-brand-500 bg-brand-50/50 ring-1 ring-brand-500'
                            : 'border-line bg-white hover:border-slate-300 hover:bg-slate-50',
                        )}
                      >
                        <Avatar name={location.name} size="md" square />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <h2 className="truncate text-sm font-semibold text-ink">{location.name}</h2>
                            {business?.location_id === location.location_id && (
                              <Badge tone="brand">Current</Badge>
                            )}
                          </div>
                          {location.address && (
                            <p className="mt-0.5 flex items-center gap-1 truncate text-[13px] text-ink-muted">
                              <MapPin className="h-3.5 w-3.5 shrink-0" aria-hidden />
                              <span className="truncate">{location.address}</span>
                            </p>
                          )}
                          <p className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
                            <span>{location.total_reviews} reviews</span>
                            <span>{location.unanswered} need a reply</span>
                            {location.average_rating != null && (
                              <span className="inline-flex items-center gap-1">
                                <Star className="h-3 w-3 fill-amber-400 text-amber-400" aria-hidden />
                                {location.average_rating.toFixed(1)} average
                              </span>
                            )}
                          </p>
                        </div>
                        <span
                          className={classNames(
                            'flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2',
                            active ? 'border-brand-600 bg-brand-600' : 'border-slate-300',
                          )}
                          aria-hidden
                        >
                          {active && <Check className="h-3 w-3 text-white" />}
                        </span>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </div>

          <div className="flex flex-col-reverse gap-2 border-t border-line bg-slate-50 px-6 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-8">
            {business ? (
              <button className="btn-ghost" onClick={() => navigate('/dashboard')}>
                Cancel
              </button>
            ) : (
              <span />
            )}
            <button
              className="btn-primary"
              onClick={handleContinue}
              disabled={loading || !!error || !selectedId}
            >
              Continue
              <ArrowRight className="h-4 w-4" aria-hidden />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
