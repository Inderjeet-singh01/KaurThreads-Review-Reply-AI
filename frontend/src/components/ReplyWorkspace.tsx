import { useCallback, useEffect, useId, useRef, useState } from 'react'
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ExternalLink,
  Loader2,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  X,
  XCircle,
} from 'lucide-react'
import { ApiError, api } from '../lib/api'
import type {
  ReplyLength,
  ReplyTone,
  ReplyValidationChecks,
  ReplyValidationResult,
  Review,
} from '../lib/types'
import { useBusiness } from '../context/BusinessContext'
import { useReviews } from '../context/ReviewsContext'
import { useToast } from '../context/ToastContext'
import { readReplyPreferences } from '../lib/replyPreferences'
import {
  MAX_REPLY_BYTES,
  byteLength,
  classNames,
  formatDateTime,
  formatFullDate,
  formatRelativeDate,
  formatTimeWithRelative,
  googleMapsUrl,
} from '../lib/utils'
import { Avatar } from './Avatar'
import { Badge, StatusBadge, type BadgeTone } from './StatusBadge'
import { ConfirmDialog } from './ConfirmDialog'
import { ErrorState } from './ErrorState'
import { RatingStars } from './RatingStars'
import { ReplyControls } from './ReplyControls'

// Unpublished drafts survive switching between reviews (in memory only).
const drafts = new Map<string, { text: string; edited: boolean }>()
const draftKey = (locationId: string | null, reviewId: string) => `${locationId ?? ''}:${reviewId}`

/** Forget every unpublished draft (tests). */
// eslint-disable-next-line react-refresh/only-export-components
export function clearReplyDrafts() {
  drafts.clear()
}

interface ReplyWorkspaceProps {
  review: Review
  /** Draft a reply as soon as the review is shown (it was opened explicitly). */
  autoGenerate: boolean
  onAuthExpired: () => void
  /** Open the next review that needs a reply (shown after publishing). */
  onNext?: () => void
}

/** The selected review and its reply composer. */
export function ReplyWorkspace({ review, autoGenerate, onAuthExpired, onNext }: ReplyWorkspaceProps) {
  const { business } = useBusiness()
  const googleUrl = business ? googleMapsUrl(business.name, business.address) : undefined

  return (
    <div className="space-y-4">
      <section className="card p-5 sm:p-6" aria-labelledby="selected-review-heading">
        <div className="flex items-start gap-3.5">
          <Avatar name={review.reviewer} photoUrl={review.profile_photo_url} size="lg" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="selected-review-heading" className="truncate text-base font-semibold text-ink">
                {review.reviewer}
              </h2>
              <StatusBadge status={review.has_reply ? 'replied' : 'needs-reply'} />
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
              <RatingStars rating={review.rating} size="md" />
              {review.created_at && (
                <time dateTime={review.created_at} className="text-[13px] text-ink-muted">
                  {formatFullDate(review.created_at)} · {formatRelativeDate(review.created_at)}
                </time>
              )}
            </div>
          </div>
        </div>

        {review.review ? (
          <p className="mt-4 whitespace-pre-wrap text-[15px] leading-relaxed text-slate-700">
            {review.review}
          </p>
        ) : (
          <p className="mt-4 text-sm italic text-slate-400">Rating only — no written comment.</p>
        )}

        {googleUrl && (
          <a
            href={googleUrl}
            target="_blank"
            rel="noreferrer"
            className="focus-ring mt-4 inline-flex items-center gap-1.5 rounded-md text-[13px] font-medium text-brand-700 hover:text-brand-800"
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            View business on Google Maps
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        )}
      </section>

      <ReplyComposer
        key={review.review_id}
        review={review}
        autoGenerate={autoGenerate}
        onAuthExpired={onAuthExpired}
        onNext={onNext}
      />
    </div>
  )
}

type ComposerStatus = { label: string; tone: BadgeTone } | null

function ReplyComposer({ review, autoGenerate, onAuthExpired, onNext }: ReplyWorkspaceProps) {
  const toast = useToast()
  const { business } = useBusiness()
  const locationId = business?.location_id ?? null
  const { markReplied, refresh } = useReviews()
  const reviewId = review.review_id
  const key = draftKey(locationId, reviewId)
  const textareaId = useId()
  const hintId = useId()

  const [reply, setReply] = useState(() => drafts.get(key)?.text ?? '')
  const [edited, setEdited] = useState(() => drafts.get(key)?.edited ?? false)
  const [tone, setTone] = useState<ReplyTone>(() => readReplyPreferences().tone)
  const [length, setLength] = useState<ReplyLength>(() => readReplyPreferences().length)
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [replaceConfirm, setReplaceConfirm] = useState(false)
  // A check result is stored with the exact text that was checked, so it is
  // never shown for a different (regenerated or edited) draft.
  const [validation, setValidation] = useState<{ text: string; result: ReplyValidationResult } | null>(null)
  const [validating, setValidating] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [publishError, setPublishError] = useState<string | null>(null)
  const [justPublished, setJustPublished] = useState(false)
  const autoGenerated = useRef(false)

  // Keep the draft when switching to another review and back.
  useEffect(() => {
    if (reply) drafts.set(key, { text: reply, edited })
    else drafts.delete(key)
  }, [key, reply, edited])

  const handleAuthError = useCallback(
    (err: unknown): boolean => {
      if (err instanceof ApiError && err.isAuth) {
        onAuthExpired()
        return true
      }
      return false
    },
    [onAuthExpired],
  )

  const generate = useCallback(
    async (opts?: { silent?: boolean }) => {
      setGenerating(true)
      setGenerateError(null)
      setValidation(null)
      setValidationError(null)
      try {
        const res = await api.generateReply(reviewId, { locationId, tone, length })
        setReply(res.reply)
        setEdited(false)
        if (!opts?.silent) toast.success('New AI draft ready. Review it before publishing.')
      } catch (err) {
        if (handleAuthError(err)) return
        const message = err instanceof ApiError ? err.message : 'Could not generate a reply.'
        setGenerateError(message)
        if (!opts?.silent) toast.error(message)
        if (err instanceof ApiError && err.status === 409) void refresh({ silent: true })
      } finally {
        setGenerating(false)
      }
    },
    [reviewId, locationId, tone, length, toast, handleAuthError, refresh],
  )

  // First draft for a review the user opened explicitly (once, and never
  // over an existing draft).
  useEffect(() => {
    if (!autoGenerate || autoGenerated.current || review.has_reply || reply) return
    autoGenerated.current = true
    void generate({ silent: true })
  }, [autoGenerate, review.has_reply, reply, generate])

  const requestRegenerate = () => (edited && reply ? setReplaceConfirm(true) : void generate())

  const checkReply = async () => {
    if (validating || !reply.trim()) return
    const text = reply
    setValidating(true)
    setValidationError(null)
    try {
      const result = await api.validateReply(reviewId, text, locationId)
      setValidation({ text, result })
    } catch (err) {
      if (handleAuthError(err)) return
      const alreadyAnswered = err instanceof ApiError && err.status === 409
      const message = alreadyAnswered ? (err as ApiError).message : 'Could not check the reply.'
      // Show the backend's specific reason (e.g. AI quota) inline.
      const detail = err instanceof ApiError && err.status >= 500 ? err.detail : ''
      setValidation(null)
      setValidationError(detail ? `${message} ${detail}` : message)
      if (alreadyAnswered) void refresh({ silent: true })
    } finally {
      setValidating(false)
    }
  }

  const publish = async () => {
    if (publishing) return
    const text = reply
    setPublishing(true)
    setPublishError(null)
    try {
      const res = await api.publishReply(reviewId, text, locationId)
      if (!res?.published) throw new Error('not confirmed')
      drafts.delete(key)
      setConfirmOpen(false)
      setJustPublished(true)
      // Counts and lists update now; the background refresh then picks up
      // Google's stored reply and its timestamp.
      markReplied([{ reviewId, reply: res.reply ?? text }])
      toast.success('Reply published to Google.')
      void refresh({ silent: true })
    } catch (err) {
      if (handleAuthError(err)) return
      setConfirmOpen(false)
      if (err instanceof ApiError && err.status === 409) {
        setPublishError(err.message)
        toast.error('Not published — this review already has a reply on Google.')
        void refresh({ silent: true })
      } else {
        const message =
          err instanceof ApiError
            ? err.message
            : 'Google did not confirm the reply. It was not published — please try again.'
        setPublishError(message)
        toast.error(message)
      }
    } finally {
      setPublishing(false)
    }
  }

  if (review.has_reply) {
    return <RepliedPanel review={review} justPublished={justPublished} onNext={onNext} />
  }

  const bytes = byteLength(reply)
  const overLimit = bytes > MAX_REPLY_BYTES
  const empty = reply.trim().length === 0
  const busy = generating || validating || publishing
  const current = validation && validation.text === reply ? validation.result : null
  const staleCheck = !!validation && validation.text !== reply

  const status: ComposerStatus = generating
    ? { label: 'Generating…', tone: 'brand' }
    : validating
      ? { label: 'Checking…', tone: 'brand' }
      : publishing
        ? { label: 'Publishing…', tone: 'brand' }
        : current
          ? current.passed
            ? { label: 'Check passed', tone: 'success' }
            : { label: 'Check failed', tone: 'danger' }
          : empty
            ? null
            : edited
              ? { label: 'Edited', tone: 'neutral' }
              : { label: 'Draft ready', tone: 'info' }

  return (
    <section className="card" aria-labelledby={`${textareaId}-title`}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4 sm:px-6">
        <h2 id={`${textareaId}-title`} className="flex items-center gap-2 text-base font-semibold text-ink">
          <Sparkles className="h-4 w-4 text-brand-600" aria-hidden />
          Your reply
          {status && <Badge tone={status.tone}>{status.label}</Badge>}
        </h2>
        {!empty && (
          <button className="btn-secondary btn-sm" onClick={requestRegenerate} disabled={busy}>
            <RefreshCw className={classNames('h-3.5 w-3.5', generating && 'animate-spin')} aria-hidden />
            Regenerate
          </button>
        )}
      </div>

      <div className="space-y-4 px-5 py-5 sm:px-6">
        <ReplyControls
          tone={tone}
          length={length}
          onToneChange={setTone}
          onLengthChange={setLength}
          disabled={busy}
        />

        {generateError && empty && !generating ? (
          <ErrorState
            compact
            title="Couldn’t generate a reply"
            message={generateError}
            onRetry={() => void generate()}
          />
        ) : (
          <div>
            <label htmlFor={textareaId} className="mb-1.5 block text-xs font-semibold text-slate-600">
              Reply text
            </label>
            <div className="relative">
              <textarea
                id={textareaId}
                value={reply}
                onChange={(e) => {
                  setReply(e.target.value)
                  setEdited(true)
                  setPublishError(null)
                }}
                disabled={generating || publishing}
                rows={7}
                aria-describedby={hintId}
                className="input min-h-[168px] resize-y leading-relaxed"
                placeholder="Generate an AI draft, or write your own reply…"
              />
              {generating && (
                <div className="absolute inset-0 flex flex-col items-center justify-center rounded-[10px] bg-white/80">
                  <Loader2 className="h-6 w-6 animate-spin text-brand-600" aria-hidden />
                  <p className="mt-2 text-sm font-medium text-slate-600">Writing a reply…</p>
                </div>
              )}
            </div>
            <div id={hintId} className="mt-1.5 flex items-center justify-between gap-3 text-xs">
              <span className={staleCheck ? 'text-amber-700' : 'text-ink-muted'}>
                {staleCheck
                  ? 'Changed since the last check — check again before publishing.'
                  : 'Nothing is posted until you confirm.'}
              </span>
              <span className={classNames('tabular-nums', overLimit ? 'font-semibold text-red-600' : 'text-ink-muted')}>
                {bytes.toLocaleString()} / {MAX_REPLY_BYTES.toLocaleString()} bytes
              </span>
            </div>
          </div>
        )}

        {empty && !generating && !generateError && (
          <button className="btn-primary w-full sm:w-auto" onClick={() => void generate()}>
            <Sparkles className="h-4 w-4" aria-hidden />
            Generate AI Reply
          </button>
        )}

        {current ? (
          <ValidationResultCard result={current} />
        ) : (
          validationError && (
            <p role="alert" className="rounded-lg bg-red-50 px-3.5 py-2.5 text-[13px] font-medium text-red-700">
              {validationError}
            </p>
          )
        )}

        {publishError && (
          <p role="alert" className="flex gap-2 rounded-lg bg-red-50 px-3.5 py-2.5 text-[13px] font-medium text-red-700">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            {publishError}
          </p>
        )}
      </div>

      {!empty && (
        <div className="flex flex-col-reverse gap-2 border-t border-line bg-slate-50/60 px-5 py-4 sm:flex-row sm:justify-end sm:gap-3 sm:px-6">
          <button className="btn-secondary" onClick={() => void checkReply()} disabled={busy || overLimit}>
            {validating ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <ShieldCheck className="h-4 w-4" aria-hidden />
            )}
            {validating ? 'Checking…' : 'Check reply'}
          </button>
          <button
            className="btn-primary"
            onClick={() => {
              setPublishError(null)
              setConfirmOpen(true)
            }}
            disabled={busy || overLimit}
          >
            <Send className="h-4 w-4" aria-hidden />
            Publish to Google
          </button>
        </div>
      )}

      <ConfirmDialog
        open={confirmOpen}
        title="Publish this reply to Google?"
        description={
          <>
            It will appear publicly under <strong className="text-ink">{review.reviewer}</strong>’s review
            on your Google Business Profile. Right before publishing, Google is checked again — if the
            review already has a reply, nothing is sent.
          </>
        }
        confirmLabel="Publish reply"
        busyLabel="Publishing…"
        busy={publishing}
        onConfirm={() => void publish()}
        onCancel={() => setConfirmOpen(false)}
      >
        <blockquote className="mt-4 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg border border-line bg-slate-50 px-4 py-3 text-sm leading-relaxed text-slate-700">
          {reply}
        </blockquote>
        <p className="mt-3 flex items-center gap-2 text-[13px]">
          {current?.passed ? (
            <>
              <CheckCircle2 className="h-4 w-4 text-green-600" aria-hidden />
              <span className="text-slate-700">This exact text passed the reply check.</span>
            </>
          ) : current ? (
            <>
              <XCircle className="h-4 w-4 text-red-600" aria-hidden />
              <span className="font-medium text-red-700">The reply check flagged this text.</span>
            </>
          ) : (
            <>
              <ShieldCheck className="h-4 w-4 text-slate-400" aria-hidden />
              <span className="text-ink-muted">This text has not been checked.</span>
            </>
          )}
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={replaceConfirm}
        title="Replace your edited reply?"
        description="A new AI draft will replace the text you edited. This can’t be undone."
        confirmLabel="Replace with new draft"
        onConfirm={() => {
          setReplaceConfirm(false)
          void generate()
        }}
        onCancel={() => setReplaceConfirm(false)}
      />
    </section>
  )
}

function RepliedPanel({
  review,
  justPublished,
  onNext,
}: {
  review: Review
  justPublished: boolean
  onNext?: () => void
}) {
  return (
    <section className="card overflow-hidden" aria-labelledby="posted-reply-heading">
      {justPublished && (
        <div role="status" className="flex items-center gap-2 bg-green-50 px-5 py-3 text-sm font-medium text-green-800 sm:px-6">
          <CheckCircle2 className="h-4 w-4" aria-hidden />
          Published to Google. It can take a few minutes to appear on Google.
        </div>
      )}
      <div className="px-5 py-5 sm:px-6">
        <h2 id="posted-reply-heading" className="flex items-center gap-2 text-base font-semibold text-ink">
          <CheckCircle2 className="h-4 w-4 text-green-600" aria-hidden />
          Your reply on Google
        </h2>
        <div className="mt-4 whitespace-pre-wrap rounded-lg border border-green-100 bg-green-50/50 px-4 py-3.5 text-sm leading-relaxed text-slate-700">
          {review.reply_comment || 'A reply has been posted for this review.'}
        </div>
        {review.reply_updated_at && (
          <p className="mt-3 text-xs text-ink-muted" title={formatDateTime(review.reply_updated_at)}>
            Replied {formatTimeWithRelative(review.reply_updated_at)}
          </p>
        )}
        {!justPublished && (
          <p className="mt-4 rounded-lg bg-slate-50 px-3.5 py-3 text-[13px] text-ink-muted">
            This review already has a reply on Google, so a new one can’t be published here.
          </p>
        )}
      </div>
      {onNext && (
        <div className="flex justify-end border-t border-line bg-slate-50/60 px-5 py-4 sm:px-6">
          <button className="btn-primary" onClick={onNext}>
            Next review needing a reply
          </button>
        </div>
      )}
    </section>
  )
}

const CHECK_LABELS: Record<keyof ReplyValidationChecks, string> = {
  review_relevance: 'Relevant to the review',
  business_relevance: 'Relevant to the business',
  no_hallucination: 'No invented details',
  appropriate_tone: 'Appropriate tone',
  safe_to_publish: 'Safe to publish',
}

function ValidationResultCard({ result }: { result: ReplyValidationResult }) {
  const passed = result.passed
  const Icon = passed ? CheckCircle2 : XCircle
  return (
    <div
      role="status"
      className={classNames(
        'rounded-lg border px-4 py-3',
        passed ? 'border-green-200 bg-green-50/60' : 'border-red-200 bg-red-50/60',
      )}
    >
      <p
        className={classNames(
          'flex items-center gap-2 text-sm font-semibold',
          passed ? 'text-green-800' : 'text-red-800',
        )}
      >
        <Icon className="h-4 w-4" aria-hidden />
        {passed ? 'Reply passed the check' : 'Reply failed the check'}
      </p>
      <p className="mt-1 text-[13px] leading-relaxed text-slate-600">
        {passed
          ? 'This reply looks suitable for this review.'
          : result.reason || 'This reply may not be suitable for this review.'}
      </p>
      <ul className="mt-2.5 grid grid-cols-1 gap-x-6 gap-y-1 text-[13px] sm:grid-cols-2">
        {(Object.keys(CHECK_LABELS) as (keyof ReplyValidationChecks)[]).map((check) => {
          const ok = result.checks[check]
          return (
            <li key={check} className="flex items-center justify-between gap-2">
              <span className="text-slate-600">{CHECK_LABELS[check]}</span>
              {ok ? (
                <Check className="h-3.5 w-3.5 text-green-600" aria-label="passed" />
              ) : (
                <X className="h-3.5 w-3.5 text-red-600" aria-label="failed" />
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
