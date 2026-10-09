import { useId, type ReactNode } from 'react'
import { Modal } from './Modal'

interface ConfirmDialogProps {
  open: boolean
  title: string
  description?: ReactNode
  /** Extra content between the description and the buttons. */
  children?: ReactNode
  confirmLabel?: string
  busyLabel?: string
  cancelLabel?: string
  tone?: 'primary' | 'danger'
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel = 'Confirm',
  busyLabel = 'Working…',
  cancelLabel = 'Cancel',
  tone = 'primary',
  busy,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const titleId = useId()
  return (
    <Modal open={open} onClose={onCancel} labelledBy={titleId} busy={busy}>
      <div className="overflow-y-auto p-6">
        <h2 id={titleId} className="text-lg font-semibold text-ink">
          {title}
        </h2>
        {description && <div className="mt-2 text-sm leading-relaxed text-slate-600">{description}</div>}
        {children}
      </div>
      <div className="flex flex-col-reverse gap-2 border-t border-line bg-slate-50 px-6 py-4 sm:flex-row sm:justify-end sm:gap-3">
        <button className="btn-secondary" onClick={onCancel} disabled={busy} data-autofocus>
          {cancelLabel}
        </button>
        <button
          className={tone === 'danger' ? 'btn-danger' : 'btn-primary'}
          onClick={onConfirm}
          disabled={busy}
        >
          {busy ? busyLabel : confirmLabel}
        </button>
      </div>
    </Modal>
  )
}
