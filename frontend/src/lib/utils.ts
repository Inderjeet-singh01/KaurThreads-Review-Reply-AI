// Small presentation helpers shared across components.

/** Relative-ish, human date: "2 days ago", "1 week ago", or a date string. */
export function formatRelativeDate(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const now = Date.now()
  const diffMs = now - date.getTime()
  const day = 24 * 60 * 60 * 1000

  // Small negative skew (client clock slightly behind the server) is "now".
  if (diffMs < -5 * 60 * 1000) return formatFullDate(iso)
  const minutes = Math.floor(Math.max(0, diffMs) / 60000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return minutes === 1 ? '1 minute ago' : `${minutes} minutes ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return hours === 1 ? '1 hour ago' : `${hours} hours ago`
  const days = Math.floor(diffMs / day)
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days < 7) return `${days} days ago`
  if (days < 14) return '1 week ago'
  if (days < 30) return `${Math.floor(days / 7)} weeks ago`
  if (days < 60) return '1 month ago'
  if (days < 365) return `${Math.floor(days / 30)} months ago`
  return formatFullDate(iso)
}

/** Full readable date: "Sep 26, 2026". */
export function formatFullDate(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/** Date with time of day: "Sep 26, 2026, 4:32 PM". */
export function formatDateTime(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

/**
 * Exact time plus a relative hint: "Sep 29, 2026, 4:32 PM · 2 days ago".
 * The hint is dropped once it would only repeat the date (older than a year).
 */
export function formatTimeWithRelative(iso: string | null): string {
  const exact = formatDateTime(iso)
  if (!exact) return ''
  const relative = formatRelativeDate(iso)
  return relative && relative !== formatFullDate(iso)
    ? `${exact} · ${relative.toLowerCase()}`
    : exact
}

/** A duration in minutes as words: 10080 -> "7 days", 0 -> "No limit". */
export function formatMinutes(minutes: number): string {
  if (minutes <= 0) return 'No limit'
  if (minutes % 1440 === 0) return `${minutes / 1440} ${minutes === 1440 ? 'day' : 'days'}`
  if (minutes % 60 === 0) return `${minutes / 60} ${minutes === 60 ? 'hour' : 'hours'}`
  return `${minutes} minutes`
}

/** Milliseconds since epoch for sorting; missing/invalid dates sort as 0. */
export function timestamp(iso: string | null): number {
  if (!iso) return 0
  const time = new Date(iso).getTime()
  return Number.isNaN(time) ? 0 : time
}

/** Initials from a display name, e.g. "Priya Sharma" -> "P". */
export function initials(name: string): string {
  const trimmed = (name || '').trim()
  if (!trimmed) return '?'
  const parts = trimmed.split(/\s+/)
  return parts[0].charAt(0).toUpperCase()
}

// Deterministic avatar background from a name (mirrors the reference's
// colorful circular avatars).
const AVATAR_COLORS = [
  'bg-rose-500',
  'bg-orange-500',
  'bg-amber-500',
  'bg-emerald-500',
  'bg-teal-500',
  'bg-sky-500',
  'bg-indigo-500',
  'bg-violet-500',
  'bg-fuchsia-500',
  'bg-pink-500',
]

export function avatarColor(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  return AVATAR_COLORS[hash % AVATAR_COLORS.length]
}

/** Byte length of a string (Google counts reply length in UTF-8 bytes). */
export function byteLength(text: string): number {
  return new TextEncoder().encode(text).length
}

export const MAX_REPLY_BYTES = 4096

export function classNames(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ')
}

/**
 * Best-effort Google Maps link for a business (the Reviews API does not expose
 * a per-review deep link, so we point to the business on Google Maps).
 */
export function googleMapsUrl(name: string, address?: string): string {
  const query = [name, address].filter(Boolean).join(' ')
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`
}
