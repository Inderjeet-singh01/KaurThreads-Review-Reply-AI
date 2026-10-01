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

export interface ReplyValidationChecks {
  review_relevance: boolean
  business_relevance: boolean
  no_hallucination: boolean
  appropriate_tone: boolean
  safe_to_publish: boolean
}

export interface ReplyValidationResult {
  review_id: string
  passed: boolean
  decision: 'PASS' | 'FAIL'
  reason: string | null
  checks: ReplyValidationChecks
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

/** GET /automation/status — read-only automatic-reply configuration. */
export interface AutomationStatus {
  enabled: boolean
  dry_run: boolean
  max_regenerations: number
  max_processing_attempts: number
  location_ids: string[]
  webhook_auth_configured: boolean
  test_endpoint_enabled: boolean
  backfill_configured: boolean
  backfill_delay_seconds: number
}

/** Bulk "reply to all pending reviews" (POST /automation/backfill). */
export type BackfillJobStatus = 'STARTING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'

export type BackfillOutcome =
  | 'PENDING'
  | 'PROCESSING'
  | 'PUBLISHED'
  | 'DRY_RUN'
  | 'SKIPPED'
  | 'FAILED'
  | 'CANCELLED'

export interface BackfillItem {
  review_id: string
  outcome: BackfillOutcome
  final_status: string | null
  run_id: string | null
  attempts: number
  generation_provider: string | null
  validation_results: string[]
  publish_result: string | null
  error_stage: string | null
  error: string | null
  retryable: boolean
  started_at: string | null
  finished_at: string | null
}

export interface BackfillJob {
  job_id: string
  status: BackfillJobStatus
  location_id: string | null
  dry_run: boolean
  total: number
  processed: number
  published: number
  would_publish: number
  skipped: number
  failed: number
  cancelled: number
  current_review_id: string | null
  cancel_requested: boolean
  started_at: string
  finished_at: string | null
  error: string | null
  items: BackfillItem[]
}

export interface BackfillStartResponse {
  started: boolean
  job_id: string | null
  location_id?: string | null
  total_reviews?: number
  dry_run?: boolean
  reason?: string
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
