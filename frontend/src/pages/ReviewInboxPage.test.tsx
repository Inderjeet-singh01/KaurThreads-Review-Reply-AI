import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import {
  installFakeBackend,
  json,
  renderApp,
  resetAppState,
  review,
  type FakeRequest,
} from '../test/fakeBackend'
import type { Review } from '../lib/types'

const PASS = {
  review_id: 'r1',
  passed: true,
  decision: 'PASS',
  reason: null,
  checks: {
    review_relevance: true,
    business_relevance: true,
    no_hallucination: true,
    appropriate_tone: true,
    safe_to_publish: true,
  },
}

let reviews: Review[]

beforeEach(() => {
  resetAppState()
  reviews = [
    review('r1', { reviewer: 'Simran Kaur', review: 'Beautiful collection', created_at: '2026-10-05T10:00:00Z' }),
    review('r2', { reviewer: 'Ravi Sharma', rating: 3, review: 'Delivery was slow', created_at: '2026-10-04T10:00:00Z' }),
    review('r3', {
      reviewer: 'Neha Verma',
      created_at: '2026-10-01T10:00:00Z',
      has_reply: true,
      reply_comment: 'Thank you Neha!',
      reply_updated_at: '2026-10-02T10:00:00Z',
    }),
  ]
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  resetAppState()
})

const generated = (text: string) => (req: FakeRequest) =>
  json({ review_id: req.path.split('/')[2], reply: text, review: reviews[0] })

const textarea = () => screen.findByLabelText('Reply text') as Promise<HTMLTextAreaElement>

describe('Review Inbox list', () => {
  it('lists reviews needing a reply and does not spend an AI call before one is opened', async () => {
    const backend = installFakeBackend(reviews)
    renderApp('/reviews')
    const list = await screen.findByRole('region', { name: 'Reviews' })
    expect(await within(list).findByText('Simran Kaur')).toBeTruthy()
    expect(within(list).getByText('Ravi Sharma')).toBeTruthy()
    expect(within(list).queryByText('Neha Verma')).toBeNull() // already replied
    expect(screen.getByText('2 reviews need a reply.')).toBeTruthy()
    expect(backend.count('POST', /\/generate$/)).toBe(0)
  })

  it('filters by status, rating and search text', async () => {
    installFakeBackend(reviews)
    renderApp('/reviews')
    const list = await screen.findByRole('region', { name: 'Reviews' })
    await within(list).findByText('Simran Kaur')

    fireEvent.click(screen.getByRole('button', { name: /Replied/ }))
    expect(within(list).getByText('Neha Verma')).toBeTruthy()
    expect(within(list).queryByText('Simran Kaur')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /^All/ }))
    fireEvent.change(screen.getByLabelText('Filter by rating'), { target: { value: '3' } })
    expect(within(list).getByText('Ravi Sharma')).toBeTruthy()
    expect(within(list).queryByText('Simran Kaur')).toBeNull()

    fireEvent.change(screen.getByLabelText('Filter by rating'), { target: { value: 'all' } })
    fireEvent.change(screen.getByLabelText('Search reviews'), { target: { value: 'beautiful' } })
    expect(within(list).getByText('Simran Kaur')).toBeTruthy()
    expect(within(list).queryByText('Ravi Sharma')).toBeNull()

    fireEvent.change(screen.getByLabelText('Search reviews'), { target: { value: 'zzz' } })
    expect(screen.getByText('No matching reviews')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(within(list).getByText('Simran Kaur')).toBeTruthy()
  })

  it('opening a review drafts a reply once, with the saved tone and length', async () => {
    localStorage.setItem('rra.replyPreferences', JSON.stringify({ tone: 'Apologetic', length: 'Short' }))
    const backend = installFakeBackend(reviews, {
      'POST /reviews/:id/generate': generated('Thank you, Simran!'),
    })
    renderApp('/reviews')
    const list = await screen.findByRole('region', { name: 'Reviews' })
    fireEvent.click(await within(list).findByText('Simran Kaur'))

    await waitFor(async () => expect((await textarea()).value).toBe('Thank you, Simran!'))
    expect(screen.getByTestId('location').textContent).toBe('/reviews/r1')
    const call = backend.calls.find((c) => c.path === '/reviews/r1/generate')!
    expect(call.query.get('tone')).toBe('Apologetic')
    expect(call.query.get('length')).toBe('Short')
    expect(backend.count('POST', '/reviews/r1/generate')).toBe(1)
  })

  it('shows a clear message for an unknown review link', async () => {
    installFakeBackend(reviews)
    renderApp('/reviews/does-not-exist')
    expect(await screen.findByText('Review not found')).toBeTruthy()
  })
})

describe('Reply workspace', () => {
  it('checks the exact edited text, and publishes only after explicit confirmation', async () => {
    const backend = installFakeBackend(reviews, {
      'POST /reviews/:id/generate': generated('Thanks for visiting!'),
      'POST /reviews/:id/validate': () => json(PASS),
      'POST /reviews/:id/publish': (req) =>
        json({ review_id: 'r1', published: true, message: 'ok', reply: (req.body as { reply: string }).reply }),
    })
    renderApp('/reviews/r1')
    const box = await textarea()
    await waitFor(() => expect(box.value).toBe('Thanks for visiting!'))

    fireEvent.change(box, { target: { value: 'Thank you so much, Simran!' } })
    expect(screen.getByText('Edited')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Check reply' }))
    expect(await screen.findByText('Reply passed the check')).toBeTruthy()
    expect(backend.calls.find((c) => c.path === '/reviews/r1/validate')!.body).toEqual({
      reply: 'Thank you so much, Simran!',
    })

    // Editing after the check invalidates it.
    fireEvent.change(box, { target: { value: 'Thank you so much, Simran!!' } })
    expect(screen.queryByText('Reply passed the check')).toBeNull()
    expect(screen.getByText(/Changed since the last check/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Publish to Google' }))
    const dialog = screen.getByRole('dialog', { name: 'Publish this reply to Google?' })
    expect(within(dialog).getByText('Thank you so much, Simran!!')).toBeTruthy()
    expect(within(dialog).getByText('This text has not been checked.')).toBeTruthy()
    expect(backend.count('POST', '/reviews/r1/publish')).toBe(0)

    // Cancel never publishes.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(backend.count('POST', '/reviews/r1/publish')).toBe(0)

    fireEvent.click(screen.getByRole('button', { name: 'Publish to Google' }))
    fireEvent.click(screen.getByRole('button', { name: 'Publish reply' }))
    expect(await screen.findByText(/Published to Google\. It can take a few minutes/)).toBeTruthy()
    expect(backend.calls.find((c) => c.path === '/reviews/r1/publish')!.body).toEqual({
      reply: 'Thank you so much, Simran!!',
    })
    expect(screen.getByText('Your reply on Google')).toBeTruthy()
    // The next pending review is one click away.
    fireEvent.click(screen.getByRole('button', { name: 'Next review needing a reply' }))
    expect(screen.getByTestId('location').textContent).toBe('/reviews/r2')
  })

  it('never claims success when Google already has a reply (409)', async () => {
    installFakeBackend(reviews, {
      'POST /reviews/:id/generate': generated('Draft'),
      'POST /reviews/:id/publish': () =>
        json({ detail: 'This review has already been replied to on Google. The reply was NOT published.' }, 409),
    })
    renderApp('/reviews/r1')
    await waitFor(async () => expect((await textarea()).value).toBe('Draft'))
    fireEvent.click(screen.getByRole('button', { name: 'Publish to Google' }))
    fireEvent.click(screen.getByRole('button', { name: 'Publish reply' }))
    expect(await screen.findByText(/The reply was NOT published/)).toBeTruthy()
    expect(screen.queryByText(/Published to Google\./)).toBeNull()
  })

  it('treats an unconfirmed publish response as not published', async () => {
    installFakeBackend(reviews, {
      'POST /reviews/:id/generate': generated('Draft'),
      'POST /reviews/:id/publish': () => json({ review_id: 'r1', published: false, message: '', reply: null }),
    })
    renderApp('/reviews/r1')
    await waitFor(async () => expect((await textarea()).value).toBe('Draft'))
    fireEvent.click(screen.getByRole('button', { name: 'Publish to Google' }))
    fireEvent.click(screen.getByRole('button', { name: 'Publish reply' }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/It was not published/)
    expect(screen.queryByText('Your reply on Google')).toBeNull()
  })

  it('asks before a regenerate replaces edited text, and keeps drafts when switching reviews', async () => {
    let n = 0
    const backend = installFakeBackend(reviews, {
      'POST /reviews/:id/generate': (req) => generated(`AI draft ${++n}`)(req),
    })
    renderApp('/reviews/r1')
    const box = await textarea()
    await waitFor(() => expect(box.value).toBe('AI draft 1'))
    fireEvent.change(box, { target: { value: 'My own words' } })

    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }))
    const dialog = screen.getByRole('dialog', { name: 'Replace your edited reply?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(box.value).toBe('My own words')
    expect(backend.count('POST', '/reviews/r1/generate')).toBe(1)

    // Switch to another review and back: the edit is still there, no new AI call.
    const list = screen.getByRole('region', { name: 'Reviews' })
    fireEvent.click(within(list).getByText('Ravi Sharma'))
    await waitFor(async () => expect((await textarea()).value).toBe('AI draft 2'))
    fireEvent.click(within(list).getByText('Simran Kaur'))
    await waitFor(async () => expect((await textarea()).value).toBe('My own words'))
    expect(backend.count('POST', '/reviews/r1/generate')).toBe(1)
  })

  it('shows a retry when generation fails, without an empty publish button', async () => {
    installFakeBackend(reviews, {
      'POST /reviews/:id/generate': () => json({ detail: 'AI unavailable' }, 502),
    })
    renderApp('/reviews/r1')
    expect(await screen.findByText('Couldn’t generate a reply')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Publish to Google' })).toBeNull()
  })

  it('shows the posted reply for an already answered review', async () => {
    installFakeBackend(reviews)
    renderApp('/reviews/r3')
    expect(await screen.findByText('Thank you Neha!')).toBeTruthy()
    expect(screen.getByText(/already has a reply on Google/)).toBeTruthy()
    expect(screen.queryByLabelText('Reply text')).toBeNull()
  })
})

describe('Bulk reply from the Overview quick action', () => {
  it('opens the confirmation (never starts on its own)', async () => {
    const backend = installFakeBackend(reviews)
    renderApp('/dashboard')
    fireEvent.click(await screen.findByRole('link', { name: /Reply to pending reviews/ }))
    expect(await screen.findByRole('dialog', { name: 'Reply to all pending reviews?' })).toBeTruthy()
    expect(screen.getByText(/2 unanswered reviews will be processed/)).toBeTruthy()
    expect(backend.count('POST', '/automation/backfill')).toBe(0)
  })
})
