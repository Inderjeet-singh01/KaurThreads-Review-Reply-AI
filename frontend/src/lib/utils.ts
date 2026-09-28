// Small presentation helpers shared across components.

/** Relative-ish, human date: "2 days ago", "1 week ago", or a date string. */
export function formatRelativeDate(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const now = Date.now()
  const diffMs = now - date.getTime()
  const day = 24 * 60 * 60 * 1000

  if (diffMs < 0) return formatFullDate(iso)
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
