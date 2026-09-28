import { useEffect, useRef, useState } from 'react'
import { Send, X } from 'lucide-react'
import type { ReplyLength, ReplyTone } from '../lib/types'
import { MAX_REPLY_BYTES, byteLength, classNames } from '../lib/utils'
import { ReplyControls } from './ReplyControls'

interface ReplyEditorModalProps {
  open: boolean
  initialText: string
  tone: ReplyTone
  length: ReplyLength
  posting: boolean
  onToneChange: (tone: ReplyTone) => void
  onLengthChange: (length: ReplyLength) => void
  onCancel: () => void
  onPost: (text: string) => void
}

export function ReplyEditorModal({
  open,
  initialText,
  tone,
  length,
  posting,
  onToneChange,
  onLengthChange,
  onCancel,
  onPost,
}: ReplyEditorModalProps) {
  const [text, setText] = useState(initialText)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Sync when a new draft is opened (preserve edits while it stays open).
  useEffect(() => {
    if (open) setText(initialText)
  }, [open, initialText])

  useEffect(() => {
    if (open) {
      const t = window.setTimeout(() => textareaRef.current?.focus(), 60)
      return () => window.clearTimeout(t)
    }
  }, [open])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && !posting) onCancel()
    }
    if (open) window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, posting, onCancel])

  if (!open) return null

  const bytes = byteLength(text)
  const overLimit = bytes > MAX_REPLY_BYTES
  const empty = text.trim().length === 0
  const canPost = !posting && !empty && !overLimit

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-slate-900/50"
        onClick={() => !posting && onCancel()}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Edit reply"
        className="relative z-10 flex max-h-[90vh] w-full max-w-lg animate-fade-in flex-col overflow-hidden rounded-2xl bg-white shadow-xl"
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <h2 className="text-base font-bold text-slate-900">Edit Reply</h2>
          <button
            onClick={() => !posting && onCancel()}
            disabled={posting}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4 overflow-y-auto px-5 py-4">
          <div>
            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={posting}
              rows={7}
              className="input resize-none leading-relaxed"
              placeholder="Write your reply…"
            />
            <div className="mt-1.5 flex justify-end">
              <span
                className={classNames(
                  'text-xs font-medium',
                  overLimit ? 'text-rose-500' : 'text-slate-400',
                )}
              >
                {bytes}/{MAX_REPLY_BYTES}
              </span>
            </div>
          </div>

          <ReplyControls
            tone={tone}
            length={length}
            onToneChange={onToneChange}
            onLengthChange={onLengthChange}
            disabled={posting}
          />
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-slate-100 bg-slate-50 px-5 py-4">
          <button className="btn-secondary" onClick={onCancel} disabled={posting}>
            Cancel
          </button>
          <button className="btn-primary" onClick={() => onPost(text)} disabled={!canPost}>
            <Send className="h-4 w-4" />
            {posting ? 'Posting…' : 'Post Reply'}
          </button>
        </div>
      </div>
    </div>
  )
}
