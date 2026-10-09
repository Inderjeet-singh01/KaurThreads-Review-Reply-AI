import { useEffect, useRef } from 'react'
import { NavLink } from 'react-router-dom'
import {
  BarChart3,
  Bot,
  Inbox,
  LayoutDashboard,
  MessageSquareReply,
  Settings,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useBusiness } from '../context/BusinessContext'
import { useReviews } from '../context/ReviewsContext'
import { classNames } from '../lib/utils'
import { Avatar } from './Avatar'
import { BrandMark } from './BrandMark'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  badge?: number
}

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { unanswered, loading } = useReviews()
  const { business } = useBusiness()
  const closeRef = useRef<HTMLButtonElement>(null)

  const items: NavItem[] = [
    { to: '/dashboard', label: 'Overview', icon: LayoutDashboard },
    { to: '/reviews', label: 'Review Inbox', icon: Inbox, badge: loading ? undefined : unanswered.length },
    { to: '/replied', label: 'Replied Reviews', icon: MessageSquareReply },
    { to: '/analytics', label: 'Analytics', icon: BarChart3 },
    { to: '/automation', label: 'Automation', icon: Bot },
    { to: '/settings', label: 'Settings', icon: Settings },
  ]

  // Mobile drawer: focus moves in on open; Escape closes it.
  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-30 bg-slate-900/50 lg:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}

      <aside
        aria-label="Main navigation"
        className={classNames(
          'fixed inset-y-0 left-0 z-40 flex w-64 flex-col bg-navy-900 text-slate-300 transition-[transform,visibility] duration-200 lg:visible lg:translate-x-0',
          open ? 'visible translate-x-0' : 'invisible -translate-x-full',
        )}
      >
        <div className="flex h-16 items-center justify-between gap-2 px-5">
          <div className="flex items-center gap-2.5">
            <BrandMark className="h-8 w-8" />
            <span className="text-[15px] font-semibold tracking-tight text-white">Review Reply AI</span>
          </div>
          <button
            ref={closeRef}
            onClick={onClose}
            className="focus-ring rounded-lg p-1.5 text-slate-400 hover:bg-navy-800 hover:text-white lg:hidden"
            aria-label="Close menu"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <nav className="scroll-area flex-1 space-y-1 overflow-y-auto px-3 py-4">
          {items.map(({ to, label, icon: Icon, badge }) => (
            <NavLink
              key={to}
              to={to}
              onClick={onClose}
              className={({ isActive }) =>
                classNames(
                  'group flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400',
                  isActive
                    ? 'bg-brand-500/20 text-white'
                    : 'text-slate-400 hover:bg-white/5 hover:text-slate-100',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon
                    className={classNames(
                      'h-[18px] w-[18px] shrink-0',
                      isActive ? 'text-brand-300' : 'text-slate-500 group-hover:text-slate-300',
                    )}
                    aria-hidden
                  />
                  <span className="flex-1">{label}</span>
                  {badge != null && badge > 0 && (
                    <span
                      className="inline-flex min-w-[22px] items-center justify-center rounded-full bg-brand-500 px-1.5 py-0.5 text-[11px] font-bold tabular-nums text-white"
                      aria-label={`${badge} need a reply`}
                    >
                      {badge}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {business && (
          <div className="m-3 rounded-xl border border-white/5 bg-white/[0.04] p-3">
            <div className="flex items-center gap-2.5">
              <Avatar name={business.name} size="sm" square />
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-white">{business.name}</p>
                <p className="truncate text-xs text-slate-400">
                  {business.address || `Location ${business.location_id}`}
                </p>
              </div>
            </div>
            <p className="mt-2.5 inline-flex items-center gap-1.5 rounded-full bg-green-500/10 px-2 py-0.5 text-[11px] font-semibold text-green-300">
              <span className="h-1.5 w-1.5 rounded-full bg-green-400" aria-hidden />
              Google connected
            </p>
          </div>
        )}
      </aside>
    </>
  )
}
