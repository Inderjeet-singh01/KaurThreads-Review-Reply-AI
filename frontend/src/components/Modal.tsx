import { useEffect, useRef, type ReactNode } from 'react'
import { classNames } from '../lib/utils'

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

interface ModalProps {
  open: boolean
  /** Close request (Escape, backdrop). Ignored while `busy`. */
  onClose: () => void
  /** id of the element naming the dialog. */
  labelledBy: string
  busy?: boolean
  size?: 'sm' | 'md' | 'lg'
  children: ReactNode
}

const SIZES = { sm: 'max-w-md', md: 'max-w-lg', lg: 'max-w-2xl' }

/**
 * Accessible modal dialog: focus moves inside (to `[data-autofocus]` or the
 * first control), Tab stays inside, Escape and the backdrop close it, the page
 * behind does not scroll, and focus returns where it was on close.
 */
export function Modal({ open, onClose, labelledBy, busy, size = 'sm', children }: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  // Latest values without re-running the open/close effect.
  const latest = useRef({ onClose, busy })
  latest.current = { onClose, busy }

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const panel = panelRef.current
    const target =
      panel?.querySelector<HTMLElement>('[data-autofocus]') ??
      panel?.querySelector<HTMLElement>(FOCUSABLE) ??
      panel
    target?.focus()
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        if (!latest.current.busy) latest.current.onClose()
        return
      }
      if (e.key !== 'Tab' || !panel) return
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
      previous?.focus?.()
    }
  }, [open])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center p-0 sm:items-center sm:p-4">
      <div
        className="absolute inset-0 bg-slate-900/50"
        onClick={() => !busy && onClose()}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        className={classNames(
          'relative z-10 flex max-h-[92vh] w-full animate-fade-in flex-col overflow-hidden rounded-t-2xl bg-white shadow-overlay focus:outline-none sm:rounded-2xl',
          SIZES[size],
        )}
      >
        {children}
      </div>
    </div>
  )
}
