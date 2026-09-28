import { useNavigate } from 'react-router-dom'
import { MapPin, Menu, Repeat } from 'lucide-react'
import { useBusiness } from '../context/BusinessContext'
import { Avatar } from './Avatar'

export function TopBar({ onOpenMenu }: { onOpenMenu: () => void }) {
  const { business } = useBusiness()
  const navigate = useNavigate()

  return (
    <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur">
      <div className="flex items-center gap-3 px-4 py-3 sm:px-6">
        <button
          onClick={onOpenMenu}
          className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 lg:hidden"
          aria-label="Open menu"
        >
          <Menu className="h-5 w-5" />
        </button>

        {business ? (
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <Avatar name={business.name} size="md" />
            <div className="min-w-0">
              <h1 className="truncate text-[15px] font-bold text-slate-900">
                {business.name}
              </h1>
              {business.address && (
                <p className="flex items-center gap-1 truncate text-xs text-slate-500">
                  <MapPin className="h-3 w-3 shrink-0" />
                  <span className="truncate">{business.address}</span>
                </p>
              )}
            </div>
          </div>
        ) : (
          <div className="flex-1" />
        )}

        <button
          onClick={() => navigate('/select-business')}
          className="btn-secondary shrink-0 !px-3 !py-2 text-sm"
        >
          <Repeat className="h-4 w-4" />
          <span className="hidden sm:inline">Change</span>
        </button>
      </div>
    </header>
  )
}
