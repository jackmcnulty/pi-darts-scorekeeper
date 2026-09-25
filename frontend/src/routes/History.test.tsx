/**
 * The history list: what it shows, and what it asks the Pi for.
 *
 * Criterion 4 has two halves and the second is the one a rendering test can
 * miss. "Paginates" is visible; "does not fetch the full history at once" is a
 * claim about the *requests*, so every test here records them and asserts the
 * `limit` and `offset` that actually went out. A screen that fetched four
 * hundred matches and sliced twenty would render identically and fail these.
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { Match } from '../api/matches'
// `main.tsx` is what imports this in the app, and it is not in the tree here.
// Without it `--touch-min` is undeclared and any floor assertion is vacuous.
import '../styles/global.css'
import { installServer, renderApp, server } from '../test-harness'
import { match } from '../matches/historyfixture'

installServer()

interface Asked {
  status: string | null
  limit: string | null
  offset: string | null
}

/**
 * Serve `GET /api/matches` from a whole history, paging it the way the Pi does,
 * and record every request.
 *
 * Built as a factory rather than a shared constant because a `Response` body can
 * only be read once: a reused module-level response throws "body object should
 * not be disturbed" as an unhandled rejection that does not fail the test it
 * came from. #25 learned that the hard way.
 */
function servesHistory(all: Match[]) {
  const asked: Asked[] = []
  server.use(
    http.get('*/api/matches', ({ request }) => {
      const query = new URL(request.url).searchParams
      const status = query.get('status')
      const limit = Number(query.get('limit') ?? '20')
      const offset = Number(query.get('offset') ?? '0')
      asked.push({ status, limit: query.get('limit'), offset: query.get('offset') })
      const filtered = status === null ? all : all.filter((entry) => entry.status === status)
      return HttpResponse.json({
        items: filtered.slice(offset, offset + limit),
        total: filtered.length,
        limit,
        offset,
      })
    }),
  )
  return asked
}

/** `count` complete matches, newest first, with distinct ids. */
function many(count: number): Match[] {
  return Array.from({ length: count }, (_, index) =>
    match({ id: 1000 + index, bestOf: 3, startScore: 501 }),
  )
}

describe('the history list', () => {
  it('asks for one page and says where in the history it is', async () => {
    const asked = servesHistory(many(57))
    renderApp('/history')

    expect(await screen.findByText('1–20 of 57')).toBeInTheDocument()
    // The whole of criterion 4's second half: one request, for twenty.
    expect(asked).toHaveLength(1)
    expect(asked[0]!.limit).toBe('20')
    expect(asked[0]!.offset).toBe('0')
    // Twenty rows on screen, not fifty-seven.
    expect(screen.getAllByRole('link', { name: /Jack v Dad/ })).toHaveLength(20)
  })

  it('pages forward by asking for the next offset, not by slicing what it had', async () => {
    const asked = servesHistory(many(57))
    renderApp('/history')
    await screen.findByText('1–20 of 57')

    await userEvent.click(screen.getByRole('button', { name: 'Older' }))

    expect(await screen.findByText('21–40 of 57')).toBeInTheDocument()
    expect(asked).toHaveLength(2)
    expect(asked[1]!.offset).toBe('20')
    expect(asked[1]!.limit).toBe('20')
  })

  it('pages back again, and reuses the page it already had', async () => {
    const asked = servesHistory(many(57))
    renderApp('/history')
    await screen.findByText('1–20 of 57')
    await userEvent.click(screen.getByRole('button', { name: 'Older' }))
    await screen.findByText('21–40 of 57')
    const afterForward = asked.length

    await userEvent.click(screen.getByRole('button', { name: 'Newer' }))

    expect(await screen.findByText('1–20 of 57')).toBeInTheDocument()
    // Page one is still cached under its own key, so going back costs no
    // request at all. Asserted rather than assumed, because a key that varied
    // per render would silently refetch every time somebody paged.
    expect(asked).toHaveLength(afterForward)
  })

  it('offers nothing newer on the first page and nothing older on the last', async () => {
    servesHistory(many(25))
    renderApp('/history')
    await screen.findByText('1–20 of 25')

    expect(screen.getByRole('button', { name: 'Newer' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Older' })).toBeEnabled()

    await userEvent.click(screen.getByRole('button', { name: 'Older' }))
    await screen.findByText('21–25 of 25')

    expect(screen.getByRole('button', { name: 'Newer' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Older' })).toBeDisabled()
  })

  it('does not page at all when everything fits on one', async () => {
    servesHistory(many(3))
    renderApp('/history')
    await screen.findByText('1–3 of 3')

    expect(screen.getByRole('button', { name: 'Newer' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Older' })).toBeDisabled()
  })

  it('filters on the server and goes back to the first page when it does', async () => {
    const all = [
      match({ id: 1, status: 'complete' }),
      match({ id: 2, status: 'abandoned' }),
      match({ id: 3, status: 'complete' }),
    ]
    const asked = servesHistory(all)
    renderApp('/history')
    await screen.findByText('1–3 of 3')

    await userEvent.click(screen.getByRole('radio', { name: 'Abandoned' }))

    await waitFor(() => {
      expect(asked.at(-1)!.status).toBe('abandoned')
    })
    // Filtering narrows the request, so the others are not fetched either.
    expect(asked.at(-1)!.offset).toBe('0')
    expect(await screen.findByText('1–1 of 1')).toBeInTheDocument()
  })

  it('sends no status at all for "All", because every status is its absence', async () => {
    const asked = servesHistory(many(3))
    renderApp('/history')
    await screen.findByText('1–3 of 3')

    expect(asked[0]!.status).toBeNull()
  })

  it('links each row to its detail screen, which is the deep link', async () => {
    servesHistory([match({ id: 42 })])
    renderApp('/history')

    const row = await screen.findByRole('link', { name: /Jack v Dad/ })
    expect(row).toHaveAttribute('href', '/history/42')
  })

  it('says so when there is no history yet, rather than showing an empty pager', async () => {
    servesHistory([])
    renderApp('/history')

    expect(await screen.findByText(/No matches yet/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Older' })).not.toBeInTheDocument()
  })
})

describe('criterion 6: an abandoned match is visibly distinguished', () => {
  it('labels the two differently, in text and not only in colour', async () => {
    servesHistory([match({ id: 1, status: 'complete' }), match({ id: 2, status: 'abandoned' })])
    renderApp('/history')

    await screen.findByText('1–2 of 2')
    // Each row's accessible name carries its status, so the distinction survives
    // for somebody who cannot see the styling. Queried by role rather than by
    // text because the filter control has a "Complete" option of its own.
    expect(screen.getByRole('link', { name: /Complete/ })).toHaveAttribute('href', '/history/1')
    expect(screen.getByRole('link', { name: /Abandoned/ })).toHaveAttribute('href', '/history/2')
  })

  it('gives the two statuses different classes, so the styling can differ', async () => {
    servesHistory([match({ id: 1, status: 'complete' }), match({ id: 2, status: 'abandoned' })])
    renderApp('/history')

    await screen.findByText('1–2 of 2')
    // Scoped to the list: `getByText` alone would find the filter's radio.
    const list = screen.getByRole('list')
    expect(list.querySelector('.history__status--complete')).toHaveTextContent('Complete')
    expect(list.querySelector('.history__status--abandoned')).toHaveTextContent('Abandoned')
  })
})

describe('when the Pi cannot be reached', () => {
  it('reports the failure and offers a retry that works', async () => {
    server.use(
      http.get('*/api/matches', () => HttpResponse.json({ detail: 'nope' }, { status: 500 })),
    )
    renderApp('/history')

    // Queries retry a 5xx three times, which is about a second of real backoff.
    const alert = await screen.findByRole('alert', {}, { timeout: 5000 })
    expect(alert).toBeInTheDocument()

    // The Pi starts answering, then the retry is pressed.
    servesHistory(many(3))
    await userEvent.click(screen.getByRole('button', { name: /Try again/i }))

    expect(await screen.findByText('1–3 of 3', {}, { timeout: 5000 })).toBeInTheDocument()
  })
})
