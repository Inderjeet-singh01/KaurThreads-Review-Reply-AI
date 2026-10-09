// Default tone and length for new AI drafts. These are generation hints the
// frontend sends with each request (not server settings), so they are kept
// per browser. Losing them only resets the defaults.

import type { ReplyLength, ReplyTone } from './types'

export const TONE_OPTIONS: ReadonlyArray<{ value: ReplyTone; label: string }> = [
  { value: 'Friendly & Professional', label: 'Friendly & Professional' },
  { value: 'Warm & Personal', label: 'Warm & Personal' },
  { value: 'Professional', label: 'Professional' },
  { value: 'Apologetic', label: 'Apologetic' },
]

export const LENGTH_OPTIONS: ReadonlyArray<{ value: ReplyLength; label: string }> = [
  { value: 'Short', label: 'Short' },
  { value: 'Medium', label: 'Medium' },
  { value: 'Long', label: 'Long' },
]

export interface ReplyPreferences {
  tone: ReplyTone
  length: ReplyLength
}

const KEY = 'rra.replyPreferences'
export const DEFAULT_REPLY_PREFERENCES: ReplyPreferences = {
  tone: 'Friendly & Professional',
  length: 'Medium',
}

export function readReplyPreferences(): ReplyPreferences {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? 'null') as Partial<ReplyPreferences> | null
    return {
      tone: TONE_OPTIONS.some((o) => o.value === raw?.tone)
        ? (raw!.tone as ReplyTone)
        : DEFAULT_REPLY_PREFERENCES.tone,
      length: LENGTH_OPTIONS.some((o) => o.value === raw?.length)
        ? (raw!.length as ReplyLength)
        : DEFAULT_REPLY_PREFERENCES.length,
    }
  } catch {
    return DEFAULT_REPLY_PREFERENCES
  }
}

export function saveReplyPreferences(preferences: ReplyPreferences): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(preferences))
  } catch {
    /* storage unavailable — defaults apply next time */
  }
}
