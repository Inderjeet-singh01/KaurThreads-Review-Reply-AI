// TypeScript types mirroring the FastAPI backend schemas (app/schemas/review.py).

export interface Review {
  review_id: string
  reviewer: string
  rating: number | null
  review: string
  created_at: string | null
  has_reply: boolean
  reply_comment: string | null
  reply_updated_at: string | null
  profile_photo_url: string | null
}

export interface GenerateReplyResponse {
  review_id: string
  reply: string
  review: Review
}

export interface PublishResult {
  review_id: string
  published: boolean
  message: string
  reply: string | null
}

export interface LocationSummary {
  location_id: string
  name: string
  address: string
  total_reviews: number
  answered: number
  unanswered: number
  average_rating: number | null
}

export interface ReviewStats {
  total_reviews: number
  answered: number
  unanswered: number
  average_rating: number | null
}

export interface AuthStatus {
  authenticated: boolean
  reason?: string
  expires_at?: string | null
  token_file?: string
}

export interface AuthorizeResponse {
  authorization_url: string
  state: string
  warning?: string
}

// Reply generation controls (frontend-only hints appended to the review text).
export type ReplyTone =
  | 'Friendly & Professional'
  | 'Warm & Personal'
  | 'Professional'
  | 'Apologetic'

export type ReplyLength = 'Short' | 'Medium' | 'Long'
