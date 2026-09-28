import { NavLink } from 'react-router-dom'
import {
  BarChart3,
  LayoutDashboard,
  MessageSquare,
  MessagesSquare,
  Settings,
  X,
} from 'lucide-react'
import { useReviews } from '../context/ReviewsContext'
import { classNames } from '../lib/utils'

interface NavItem {
  to: string
  label: string
  icon: typeof LayoutDashboard
  badge?: number
}

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { unanswered } = useReviews()

  const items: NavItem[] = [
    { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { to: '/reviews', label: 'Reviews', icon: MessageSquare, badge: unanswered.length },
    { to: '/replied', label: 'Replied Reviews', icon: MessagesSquare },
    { to: '/analytics', label: 'Analytics', icon: BarChart3 },
    { to: '/settings', label: 'Settings', icon: Settings },
  ]

  return (
    <>
      {/* Mobile overlay */}
      {open && (
        <div
          className="fixed inset-0 z-30 bg-slate-900/50 lg:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}

      <aside
        className={classNames(
          'fixed inset-y-0 left-0 z-40 flex w-64 flex-col bg-navy-900 text-slate-300 transition-transform duration-200 lg:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        {/* Brand */}
        <div className="flex h-16 items-center justify-between gap-2 px-5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600">
              <MessagesSquare className="h-4.5 w-4.5 text-white" />
            </div>
            <span className="text-[15px] font-bold text-white">Review Reply AI</span>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-navy-800 hover:text-white lg:hidden"
            aria-label="Close menu"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 space-y-1 px-3 py-3">
          {items.map(({ to, label, icon: Icon, badge }) => (
            <NavLink
              key={to}
              to={to}
              onClick={onClose}
              className={({ isActive }) =>
                classNames(
                  'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-brand-600 text-white shadow-sm'
                    : 'text-slate-300 hover:bg-navy-800 hover:text-white',
                )
              }
            >
              <Icon className="h-[18px] w-[18px]" />
              <span className="flex-1">{label}</span>
              {badge != null && badge > 0 && (
                <span className="inline-flex min-w-[20px] items-center justify-center rounded-full bg-rose-500 px-1.5 py-0.5 text-[11px] font-bold text-white">
                  {badge}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="px-5 py-4 text-xs text-slate-500">
          <p>Powered by AI · Google Business Profile</p>
        </div>
      </aside>
    </>
  )
}
