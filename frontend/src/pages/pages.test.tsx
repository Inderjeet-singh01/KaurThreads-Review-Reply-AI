import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import {
  AUTOMATION,
  BUSINESS,
  installFakeBackend,
  json,
  renderApp,
  resetAppState,
  review,
} from '../test/fakeBackend'
import type { Review } from '../lib/types'

const today = new Date()
const daysAgo = (n: number) => new Date(today.getTime() - n * 86_400_000).toISOString()

let reviews: Review[]

beforeEach(() => {
  resetAppState()
  reviews = [
    review('r1', { reviewer: 'Simran Kaur', rating: 5, created_at: daysAgo(1) }),
    review('r2', { reviewer: 'Ravi Sharma', rating: 4, created_at: daysAgo(3) }),
    review('r3', {
      reviewer: 'Neha Verma',
      rating: 3,
      created_at: daysAgo(60),
      has_reply: true,
      reply_comment: 'Thanks',
      reply_updated_at: daysAgo(59),
    }),
  ]
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  resetAppState()
})

const metric = (label: string) => screen.getByText(label).closest('.card') as HTMLElement

describe('Overview', () => {
  it('shows metrics computed from the real reviews, with no invented trends', async () => {
    installFakeBackend(reviews)
    renderApp('/dashboard')
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeTruthy()
    await waitFor(() => expect(within(metric('Total reviews')).getByText('3')).toBeTruthy())
    expect(within(metric('Total reviews')).getByText('2 in the last 30 days')).toBeTruthy()
    expect(within(metric('Needs reply')).getByText('2')).toBeTruthy()
    expect(within(metric('Average rating')).getByText('4.0')).toBeTruthy()
    expect(within(metric('Response rate')).getByText('33%')).toBeTruthy()
    expect(within(metric('Response rate')).getByText('Replied to 1 of 3')).toBeTruthy()
    // No growth percentages anywhere.
    expect(screen.queryByText(/^[+↑-]\s?\d+%$/)).toBeNull()

    const recent = screen.getByRole('region', { name: 'Recent reviews needing a reply' })
    expect(within(recent).getByText('Simran Kaur')).toBeTruthy()
    expect(within(recent).queryByText('Neha Verma')).toBeNull()
    expect(within(screen.getByRole('list', { name: 'Rating distribution' })).getAllByRole('listitem')).toHaveLength(5)
  })

  it('shows an error with a retry instead of zeros when reviews cannot load', async () => {
    installFakeBackend(reviews, { 'GET /reviews/all': () => json({ detail: 'down' }, 503) })
    renderApp('/dashboard')
    expect(await screen.findByText('Something went wrong')).toBeTruthy()
    expect(screen.queryByText('Total reviews')).toBeNull()
  })
})

describe('App shell', () => {
  it('has the six sections and the real pending count on Review Inbox', async () => {
    installFakeBackend(reviews)
    renderApp('/dashboard')
    const nav = await screen.findByRole('complementary', { name: 'Main navigation' })
    for (const label of ['Overview', 'Review Inbox', 'Replied Reviews', 'Analytics', 'Automation', 'Settings']) {
      expect(within(nav).getByRole('link', { name: new RegExp(label) })).toBeTruthy()
    }
    expect(await within(nav).findByLabelText('2 need a reply')).toBeTruthy()
    expect(within(nav).getByText('Kaur Threads')).toBeTruthy()
  })

  it('switches business from the header and reloads its reviews', async () => {
    const other = { ...BUSINESS, location_id: 'loc2', name: 'Kaur Threads Ludhiana', address: '' }
    const backend = installFakeBackend(reviews, { 'GET /locations': () => json([BUSINESS, other]) })
    renderApp('/reviews/r1')
    fireEvent.click(await screen.findByRole('button', { name: /Switch business/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Kaur Threads Ludhiana/ }))
    await waitFor(() =>
      expect(backend.calls.some((c) => c.path === '/reviews/all' && c.query.get('location_id') === 'loc2')).toBe(true),
    )
    // A review of the previous business is not kept open.
    expect(screen.getByTestId('location').textContent).toBe('/reviews')
    expect(JSON.parse(localStorage.getItem('rra.selectedBusiness')!).location_id).toBe('loc2')
  })
})

describe('Analytics', () => {
  it('filters by date range and offers the trend as a table', async () => {
    installFakeBackend(reviews)
    renderApp('/analytics')
    await waitFor(() => expect(within(metric('Reviews received')).getByText('2')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('Date range'), { target: { value: 'all' } })
    expect(within(metric('Reviews received')).getByText('3')).toBeTruthy()
    expect(within(metric('Response rate')).getByText('33%')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('Date range'), { target: { value: '7d' } })
    fireEvent.click(screen.getByRole('button', { name: 'View as table' }))
    const rows = within(screen.getByRole('table')).getAllByRole('row')
    expect(rows).toHaveLength(8) // header + 7 days
  })
})

describe('Automation', () => {
  it('shows the real server status read-only, with warnings and the last run', async () => {
    installFakeBackend(reviews, {
      'GET /automation/status': () =>
        json({
          ...AUTOMATION,
          dry_run: true,
          webhook_auth_configured: false,
          reconciliation_enabled: true,
          reconciliation_auth_configured: true,
          reconciliation_max_reviews: 10,
          reconciliation_lookback_minutes: 10080,
          last_reconciliation: {
            job_id: 'j1',
            status: 'COMPLETED',
            trigger: 'scheduler',
            location_id: null,
            dry_run: true,
            lookback_minutes: 10080,
            max_reviews: 10,
            unanswered_recent: 4,
            total: 4,
            processed: 4,
            published: 0,
            would_publish: 3,
            skipped: 1,
            failed: 0,
            deferred: 0,
            started_at: daysAgo(0),
            finished_at: daysAgo(0),
            error: null,
          },
        }),
    })
    renderApp('/automation')
    expect(await screen.findByText('Automatic replies are in dry-run mode')).toBeTruthy()
    expect(screen.getByText(/Pub\/Sub push authentication is not configured/)).toBeTruthy()
    expect(screen.getByText('7 days')).toBeTruthy()
    expect(within(screen.getByText('Would publish').parentElement!).getByText('3')).toBeTruthy()
    // Read-only: nothing on the page changes server settings.
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('reports a load failure with a retry', async () => {
    let fail = true
    installFakeBackend(reviews, {
      'GET /automation/status': () => (fail ? json({ detail: 'x' }, 503) : json(AUTOMATION)),
    })
    renderApp('/automation')
    expect(await screen.findByText('Couldn’t load the automation status')).toBeTruthy()
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Automatic replies are on')).toBeTruthy()
  })
})

describe('Settings', () => {
  it('disconnects only after confirmation', async () => {
    const backend = installFakeBackend(reviews, {
      'POST /auth/google/logout': () => json({ authenticated: false, message: 'ok' }),
    })
    renderApp('/settings')
    const button = await screen.findByRole('button', { name: 'Disconnect Google' })
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(button)
    const dialog = screen.getByRole('dialog', { name: 'Disconnect your Google account?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(backend.count('POST', '/auth/google/logout')).toBe(0)

    fireEvent.click(button)
    fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/login'))
    expect(backend.count('POST', '/auth/google/logout')).toBe(1)
    expect(localStorage.getItem('rra.selectedBusiness')).toBeNull()
  })

  it('saves the default reply style', async () => {
    installFakeBackend(reviews)
    renderApp('/settings')
    fireEvent.change(await screen.findByLabelText('Tone'), { target: { value: 'Warm & Personal' } })
    expect(JSON.parse(localStorage.getItem('rra.replyPreferences')!)).toEqual({
      tone: 'Warm & Personal',
      length: 'Medium',
    })
  })
})
