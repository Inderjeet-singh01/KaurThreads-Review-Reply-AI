import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ApiError, api } from '../lib/api'
import type { AutomationStatus, BackfillItem, BackfillJob } from '../lib/types'
import { BulkReplyPanel } from './BulkReplyPanel'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    api: {
      getAutomationStatus: vi.fn(),
      startBackfill: vi.fn(),
      getBackfill: vi.fn(),
      getLatestBackfill: vi.fn(),
      cancelBackfill: vi.fn(),
    },
  }
})

const mocked = vi.mocked(api)
const LOCATION = '12544756209946726867'

const automation = (overrides: Partial<AutomationStatus> = {}): AutomationStatus => ({
  enabled: true,
  dry_run: false,
  max_regenerations: 1,
  max_processing_attempts: 3,
  location_ids: [],
  webhook_auth_configured: true,
  test_endpoint_enabled: false,
  backfill_delay_seconds: 2,
  ...overrides,
})

const item = (overrides: Partial<BackfillItem> = {}): BackfillItem => ({
  review_id: 'r1',
  outcome: 'PUBLISHED',
  final_status: 'PUBLISHED',
  run_id: 'run1',
  attempts: 1,
  generation_provider: 'groq',
  validation_results: ['PASS'],
  publish_result: 'published',
  error_stage: null,
  error: null,
  retryable: false,
  started_at: null,
  finished_at: null,
  ...overrides,
})

const job = (overrides: Partial<BackfillJob> = {}): BackfillJob => ({
  job_id: 'job1',
  status: 'RUNNING',
  location_id: LOCATION,
  dry_run: false,
  total: 30,
  processed: 12,
  published: 10,
  would_publish: 0,
  skipped: 1,
  failed: 1,
  cancelled: 0,
  current_review_id: 'r13',
  cancel_requested: false,
  started_at: '2026-10-01T10:00:00+00:00',
  finished_at: null,
  error: null,
  items: [],
  ...overrides,
})

const completed = (overrides: Partial<BackfillJob> = {}) =>
  job({
    status: 'COMPLETED',
    processed: 30,
    published: 27,
    skipped: 2,
    failed: 1,
    current_review_id: null,
    finished_at: '2026-10-01T10:05:00+00:00',
    items: [
      item({
        review_id: 'failed-review',
        outcome: 'FAILED',
        final_status: 'ERROR',
        error_stage: 'fetch',
        error: 'GoogleAPIError: Google API request failed with HTTP 500',
      }),
    ],
    ...overrides,
  })

function renderPanel(props: Partial<Parameters<typeof BulkReplyPanel>[0]> = {}) {
  const onFinished = vi.fn()
  const onAuthExpired = vi.fn()
  render(
    <MemoryRouter>
      <BulkReplyPanel
        locationId={LOCATION}
        pendingCount={30}
        onFinished={onFinished}
        onAuthExpired={onAuthExpired}
        pollIntervalMs={10}
        {...props}
      />
    </MemoryRouter>,
  )
  return { onFinished, onAuthExpired }
}

const startButton = () => screen.findByRole('button', { name: /Reply to All Pending Reviews/ })

async function confirmStart() {
  fireEvent.click(await startButton())
  fireEvent.click(screen.getByRole('button', { name: 'Start Bulk Reply' }))
}

beforeEach(() => {
  localStorage.clear()
  mocked.getAutomationStatus.mockResolvedValue(automation())
  mocked.getLatestBackfill.mockResolvedValue({ job: null })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('BulkReplyPanel button and confirmation', () => {
  it('shows the real pending count and asks for confirmation before starting', async () => {
    renderPanel()
    const button = await startButton()
    expect(button.textContent).toBe('Reply to All Pending Reviews (30)')
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(button)
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText('Reply to all pending reviews?')).toBeTruthy()
    expect(screen.getByText(/30 unanswered reviews will be processed automatically/)).toBeTruthy()
    expect(screen.getByText('Replies will be published to Google.')).toBeTruthy()
    expect(mocked.startBackfill).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(mocked.startBackfill).not.toHaveBeenCalled()
  })

  it('states dry-run mode honestly', async () => {
    mocked.getAutomationStatus.mockResolvedValue(automation({ dry_run: true }))
    renderPanel()
    const button = await startButton()
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(button)
    expect(
      screen.getByText('Dry run is enabled. Replies will be generated but not published.'),
    ).toBeTruthy()
    expect(screen.queryByText('Replies will be published to Google.')).toBeNull()
  })

  it('is disabled with no pending reviews or when the server cannot run it', async () => {
    renderPanel({ pendingCount: 0 })
    const button = (await startButton()) as HTMLButtonElement
    expect(button.textContent).toContain('(0)')
    await waitFor(() => expect(mocked.getAutomationStatus).toHaveBeenCalled())
    expect(button.disabled).toBe(true)
    cleanup()

    mocked.getAutomationStatus.mockResolvedValue(automation({ enabled: false }))
    renderPanel()
    expect(await screen.findByText(/AUTO_REPLY_ENABLED=false/)).toBeTruthy()
    expect(((await startButton()) as HTMLButtonElement).disabled).toBe(true)
  })

  it('starts with one confirmation click and no key', async () => {
    mocked.startBackfill.mockResolvedValue({ started: true, job_id: 'job1', total_reviews: 30 })
    mocked.getBackfill.mockResolvedValue(job())
    renderPanel()
    await waitFor(async () => expect(((await startButton()) as HTMLButtonElement).disabled).toBe(false))
    await confirmStart()
    expect(screen.queryByLabelText(/key/i)).toBeNull()
    expect(await screen.findByText('Bulk Reply in Progress')).toBeTruthy()
    expect(mocked.startBackfill).toHaveBeenCalledWith(LOCATION)
  })
})

describe('BulkReplyPanel progress and completion', () => {
  it('replaces the button with progress while running, polls, then shows completion', async () => {
    mocked.startBackfill.mockResolvedValue({ started: true, job_id: 'job1', total_reviews: 30 })
    mocked.getBackfill
      .mockResolvedValueOnce(job({ processed: 1, published: 1, skipped: 0, failed: 0 }))
      .mockResolvedValueOnce(job())
      .mockResolvedValue(completed())
    const { onFinished } = renderPanel()
    await waitFor(async () => expect(((await startButton()) as HTMLButtonElement).disabled).toBe(false))
    await confirmStart()

    expect(mocked.startBackfill).toHaveBeenCalledWith(LOCATION)
    expect(await screen.findByText('Bulk Reply in Progress')).toBeTruthy()
    // The start button is gone while the job runs (cannot start twice).
    expect(screen.queryByRole('button', { name: /Reply to All Pending Reviews/ })).toBeNull()
    expect(await screen.findByText('12 / 30 processed')).toBeTruthy()
    expect(screen.getByText(/Current: Processing review…/)).toBeTruthy()

    expect(await screen.findByText('Bulk reply completed')).toBeTruthy()
    expect(mocked.getBackfill.mock.calls.length).toBeGreaterThanOrEqual(3)
    expect(screen.getByText('Processed: 30')).toBeTruthy()
    expect(screen.getByText('27')).toBeTruthy()
    expect(onFinished).toHaveBeenCalledTimes(1)

    // Failed reviews with reasons.
    fireEvent.click(screen.getByRole('button', { name: /Show failed reviews \(1\)/ }))
    expect(screen.getByText(/fetching the review from Google failed — Google API request failed with HTTP 500/)).toBeTruthy()
    expect(screen.queryByText(/GoogleAPIError/)).toBeNull()

    // Dismissing returns to the button.
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(await startButton()).toBeTruthy()
    expect(localStorage.getItem(`rra.backfillJob.${LOCATION}`)).toBeNull()
  })

  it('shows would-publish counts for a dry run', async () => {
    localStorage.setItem(`rra.backfillJob.${LOCATION}`, 'job1')
    mocked.getBackfill.mockResolvedValue(
      completed({ dry_run: true, published: 0, would_publish: 27 }),
    )
    renderPanel()
    expect(await screen.findByText('Bulk reply completed')).toBeTruthy()
    expect(screen.getByText('Dry run')).toBeTruthy()
    expect(screen.getByText('Would publish:')).toBeTruthy()
  })

  it('recovers a running job after a page refresh', async () => {
    localStorage.setItem(`rra.backfillJob.${LOCATION}`, 'job1')
    mocked.getBackfill.mockResolvedValue(job())
    renderPanel()
    expect(await screen.findByText('Bulk Reply in Progress')).toBeTruthy()
    expect(mocked.getBackfill).toHaveBeenCalledWith('job1')
  })

  it('finds a job running on the server without a stored id', async () => {
    mocked.getLatestBackfill.mockResolvedValue({ job: job({ job_id: 'other' }) })
    mocked.getBackfill.mockResolvedValue(job({ job_id: 'other' }))
    renderPanel()
    expect(await screen.findByText('Bulk Reply in Progress')).toBeTruthy()
    expect(localStorage.getItem(`rra.backfillJob.${LOCATION}`)).toBe('other')
  })

  it('does not stay stuck when the server forgot the job (restart)', async () => {
    localStorage.setItem(`rra.backfillJob.${LOCATION}`, 'job1')
    mocked.getBackfill.mockRejectedValue(new ApiError('Not found', 404, 'not found'))
    renderPanel()
    expect(await screen.findByText(/The last bulk reply was interrupted/)).toBeTruthy()
    expect(await startButton()).toBeTruthy()
    expect(localStorage.getItem(`rra.backfillJob.${LOCATION}`)).toBeNull()
  })

  it('stops polling after repeated network errors and offers a retry', async () => {
    localStorage.setItem(`rra.backfillJob.${LOCATION}`, 'job1')
    mocked.getBackfill
      .mockResolvedValueOnce(job())
      .mockRejectedValue(new ApiError('Cannot reach the server.', 0))
    renderPanel()
    expect(await screen.findByText(/Lost connection to the server/)).toBeTruthy()
    const calls = mocked.getBackfill.mock.calls.length
    await new Promise((r) => setTimeout(r, 60))
    expect(mocked.getBackfill.mock.calls.length).toBe(calls)

    mocked.getBackfill.mockResolvedValue(completed())
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
    expect(await screen.findByText('Bulk reply completed')).toBeTruthy()
  })

  it('can stop the job after the current review', async () => {
    localStorage.setItem(`rra.backfillJob.${LOCATION}`, 'job1')
    mocked.getBackfill.mockResolvedValue(job())
    mocked.cancelBackfill.mockResolvedValue(job({ cancel_requested: true }))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Stop after current review' }))
    await waitFor(() => expect(mocked.cancelBackfill).toHaveBeenCalledWith('job1'))
  })
})

describe('BulkReplyPanel errors', () => {
  async function startWith(error: unknown) {
    mocked.startBackfill.mockRejectedValue(error)
    const handlers = renderPanel()
    await waitFor(async () => expect(((await startButton()) as HTMLButtonElement).disabled).toBe(false))
    await confirmStart()
    return handlers
  }

  it('network error / backend unavailable', async () => {
    await startWith(new ApiError('Cannot reach the server. Check your connection and that the backend is running.', 0))
    expect((await screen.findByRole('alert')).textContent).toMatch(/Cannot reach the server/)
    cleanup()
    await startWith(new ApiError('The service is temporarily unavailable.', 503, ''))
    expect((await screen.findByRole('alert')).textContent).toMatch(/temporarily unavailable/)
  })

  it('Google API error while listing reviews', async () => {
    await startWith(new ApiError('We couldn’t reach Google right now. Please try again shortly.', 502, 'HTTP 500'))
    expect((await screen.findByRole('alert')).textContent).toMatch(/couldn’t reach Google/)
  })

  it('Google authentication error signs out', async () => {
    const { onAuthExpired } = await startWith(new ApiError('expired', 401, 'OAuth'))
    await waitFor(() => expect(onAuthExpired).toHaveBeenCalled())
  })

  it('job already running resumes its progress', async () => {
    mocked.getBackfill.mockResolvedValue(job({ job_id: 'running' }))
    await startWith(
      new ApiError('A backfill job is already running.', 409, 'A backfill job is already running.', {
        started: false,
        reason: 'A backfill job is already running.',
        job_id: 'running',
      }),
    )
    expect(await screen.findByText('Bulk Reply in Progress')).toBeTruthy()
    expect(screen.getByText(/already running — showing its progress/)).toBeTruthy()
    expect(mocked.getBackfill).toHaveBeenCalledWith('running')
  })

  it('a failed job shows a safe message', async () => {
    localStorage.setItem(`rra.backfillJob.${LOCATION}`, 'job1')
    mocked.getBackfill.mockResolvedValue(completed({ status: 'FAILED', error: 'RuntimeError: x' }))
    renderPanel()
    expect(await screen.findByText('Bulk reply failed')).toBeTruthy()
    expect(screen.getByText(/stopped unexpectedly/)).toBeTruthy()
    expect(screen.queryByText(/RuntimeError/)).toBeNull()
  })

  it('automation settings cannot be loaded', async () => {
    mocked.getAutomationStatus.mockRejectedValue(new ApiError('down', 0))
    renderPanel()
    expect(await screen.findByText(/Could not load the automation settings/)).toBeTruthy()
    expect(((await startButton()) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('BulkReplyPanel live updates and re-runs', () => {
  it('reports each review as soon as it is published, not only at the end', async () => {
    localStorage.setItem(`rra.backfillJob.${LOCATION}`, 'job1')
    mocked.getBackfill
      .mockResolvedValueOnce(job({ items: [item({ review_id: 'a' }), item({ review_id: 'b', outcome: 'PROCESSING' })] }))
      .mockResolvedValueOnce(job({ items: [item({ review_id: 'a' }), item({ review_id: 'b' })] }))
      .mockResolvedValue(completed({ items: [item({ review_id: 'a' }), item({ review_id: 'b' })] }))
    const onReviewsPublished = vi.fn()
    renderPanel({ onReviewsPublished })
    expect(await screen.findByText('Bulk reply completed')).toBeTruthy()
    // Once per review, in the poll where it was published.
    expect(onReviewsPublished.mock.calls).toEqual([[['a']], [['b']]])
  })

  it('never reports dry-run reviews as published', async () => {
    localStorage.setItem(`rra.backfillJob.${LOCATION}`, 'job1')
    mocked.getBackfill.mockResolvedValue(
      completed({ dry_run: true, items: [item({ outcome: 'DRY_RUN', final_status: 'DRY_RUN' })] }),
    )
    const onReviewsPublished = vi.fn()
    renderPanel({ onReviewsPublished })
    expect(await screen.findByText('Bulk reply completed')).toBeTruthy()
    expect(onReviewsPublished).not.toHaveBeenCalled()
  })

  it('lists skipped reviews with a reason and the reviewer name', async () => {
    localStorage.setItem(`rra.backfillJob.${LOCATION}`, 'job1')
    mocked.getBackfill.mockResolvedValue(
      completed({
        items: [
          item({ review_id: 's1', outcome: 'SKIPPED', final_status: 'SKIPPED_ALREADY_REPLIED' }),
        ],
      }),
    )
    renderPanel({ reviewLabel: (id) => (id === 's1' ? 'Priya Sharma' : undefined) })
    fireEvent.click(await screen.findByRole('button', { name: /Show skipped reviews \(1\)/ }))
    expect(screen.getByText('Priya Sharma')).toBeTruthy()
    expect(screen.getByText(/already answered on Google/)).toBeTruthy()
  })

  it('can start again for the remaining reviews straight from the result', async () => {
    localStorage.setItem(`rra.backfillJob.${LOCATION}`, 'job1')
    mocked.getBackfill.mockResolvedValueOnce(completed())
    mocked.startBackfill.mockResolvedValue({ started: true, job_id: 'job2', total_reviews: 8 })
    renderPanel({ pendingCount: 8 })
    expect(await screen.findByText('Bulk reply completed')).toBeTruthy()
    expect(screen.getByText('8 reviews are still unanswered.')).toBeTruthy()
    mocked.getBackfill.mockResolvedValue(job({ job_id: 'job2', total: 8, processed: 0 }))
    fireEvent.click(await startButton())
    fireEvent.click(screen.getByRole('button', { name: 'Start Bulk Reply' }))
    expect(await screen.findByText('Bulk Reply in Progress')).toBeTruthy()
    expect(mocked.startBackfill).toHaveBeenCalledWith(LOCATION)
  })
})
