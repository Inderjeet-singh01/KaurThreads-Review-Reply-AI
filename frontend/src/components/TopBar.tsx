import { Menu } from 'lucide-react'
import { BrandMark } from './BrandMark'

/** Small-screen header: opens the navigation drawer. Desktop uses the fixed sidebar. */
export function TopBar({ onOpenMenu }: { onOpenMenu: () => void }) {
  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-white/95 px-4 backdrop-blur lg:hidden">
      <button
        onClick={onOpenMenu}
        className="focus-ring -ml-1.5 rounded-lg p-2 text-slate-600 hover:bg-slate-100"
        aria-label="Open menu"
      >
        <Menu className="h-5 w-5" />
      </button>
      <BrandMark className="h-7 w-7" />
      <span className="text-[15px] font-semibold text-ink">Review Reply AI</span>
    </header>
  )
}
