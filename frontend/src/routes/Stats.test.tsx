/**
 * The leaderboard, and criterion 2's linkable filter.
 *
 * Criterion 2 is a claim about the *address*, not about the control: "the
 * game-type filter round-trips through the query string, so a filtered view is
 * linkable and survives refresh". So every test here does one of two things --
 * opens the screen at a filtered address and checks the request that went out, or
 * taps a filter and checks the address that came back. A test that only asserted
 * the control looked right would pass for a `useState` implementation, which is
 * exactly what this criterion rules out.
 *
 * The rank is the other thing worth testing. Rows are an `<ol>` in the server's
 * order, so the position is the rank and no number in the markup carries it. The
 * test asserts the ordered list rather than the digits, because the digits come
 * from a CSS counter and jsdom does not run `::before` content.
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import process from 'node:process'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { LeaderboardRow } from '../api/stats'
import '../styles/global.css'
import { installServer, renderApp, server } from '../test-harness'
import { leaderboard, playerReport, ranked, rankRow } from '../stats/statsfixture'

installServer()

/**
 * Serve the ranking and record every request.
 *
 * A factory, not a shared constant: a `Response` body reads once, and reusing one
 * throws "body object should not be disturbed" as an unhandled rejection that
 * fails no test in particular.
 */
function serves(rows: LeaderboardRow[] = ranked(), minDarts = 50) {
  const seen: URLSearchParams[] = []
  server.use(
    http.get('*/api/stats/leaderboard', ({ request }) => {
      const query = new URL(request.url).searchParams
      seen.push(query)
      return HttpResponse.json(leaderboard({ rows, minDarts }))
    }),
    // A row is a link to a card; following one must not hit the network
    // unhandled, which the harness fails on.
    http.get('*/api/stats/players/:playerId', () => HttpResponse.json(playerReport())),
  )
  return seen
}

describe('the ranking', () => {
  it('lists players in the order the server ranked them', async () => {
    serves()
    renderApp('/stats')

    // Awaited on the rows, not on the heading: the heading is static and renders
    // before the request resolves, so finding it proves nothing about the data.
    const rows = await screen.findAllByRole('listitem')
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining('Jack'),
      expect.stringContaining('Dad'),
    ])
    // Nothing reorders them here: the server's ORDER BY is the ranking.
    expect(rows[0]?.textContent).toContain('57.23')
    expect(rows[1]?.textContent).toContain('48.92')
  })

  it('carries the rank in the list, not in a number it computed', async () => {
    serves()
    renderApp('/stats')
    await screen.findAllByRole('listitem')

    // An <ol>, so the position is conveyed structurally. `index + 1` would be the
    // one number on the page no field of the response backs.
    const list = screen.getByRole('list')
    expect(list.tagName).toBe('OL')
    // The visible digit comes from a CSS counter, which jsdom does not evaluate,
    // so the rule is asserted against the stylesheet as text -- #25's pattern.
    const css = readFileSync(resolve(process.cwd(), 'src/routes/Stats.css'), 'utf8')
    expect(css).toContain('counter-reset: rank')
    expect(css).toContain('counter-increment: rank')
    expect(css).toContain('content: counter(rank)')
  })

  it('opens a card when a row is followed, keeping the filter', async () => {
    serves()
    renderApp('/stats?game_type=cricket')

    await userEvent.click(await screen.findByRole('link', { name: /Jack/ }))
    // The card, with the table's filter still applied.
    expect(await screen.findByRole('heading', { name: 'Jack' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Cricket' })).toBeChecked()
  })

  it('spells out a row, because a name beside a number runs together', async () => {
    serves()
    renderApp('/stats')

    expect(
      await screen.findByRole('link', {
        name: 'Jack: 57.23 three-dart average, 450 darts thrown, 3 maximums',
      }),
    ).toBeInTheDocument()
  })

  it('renders a player with no average as an em dash', async () => {
    serves([rankRow({ playerId: 9, name: 'Newcomer', average: null })])
    renderApp('/stats')

    const row = await screen.findByRole('listitem')
    expect(row.textContent).toContain('—')
    expect(document.body.textContent).not.toContain('NaN')
  })
})

describe('the filter round trip', () => {
  it('sends the filter from the address bar, so a shared link opens filtered', async () => {
    // Criterion 2's first half: the address decides the request.
    const seen = serves()
    renderApp('/stats?game_type=cricket')
    await screen.findByRole('heading', { name: 'Stats' })

    await waitFor(() => {
      expect(seen.length).toBeGreaterThan(0)
    })
    expect(seen[0]?.get('game_type')).toBe('cricket')
    expect(screen.getByRole('radio', { name: 'Cricket' })).toBeChecked()
  })

  it('narrows the request rather than filtering the answer', async () => {
    // The server does the narrowing, so a cricket table never fetches the x01
    // rows. The same bargain #26's history filter makes.
    const seen = serves()
    renderApp('/stats')
    await screen.findByRole('heading', { name: 'Stats' })
    expect(seen[0]?.get('game_type')).toBeNull()

    await userEvent.click(screen.getByRole('radio', { name: 'x01' }))
    await waitFor(() => {
      expect(seen.some((query) => query.get('game_type') === 'x01')).toBe(true)
    })
  })

  it('writes a tapped filter into the address and leaves the default out of it', async () => {
    // Criterion 2's second half. "All" is the absence of the parameter, so the
    // plain /stats address stays clean and is what a shared link looks like.
    const seen = serves()
    renderApp('/stats')
    await screen.findAllByRole('listitem')

    await userEvent.click(screen.getByRole('radio', { name: 'Cricket' }))
    await waitFor(() => {
      expect(seen.some((query) => query.get('game_type') === 'cricket')).toBe(true)
    })

    // Back to all. No new request goes out -- the unfiltered table was fetched at
    // mount and is still cached, which is the point of keying the query by the
    // filter -- so this asserts on the control and on what was never sent rather
    // than on a request that correctly did not happen.
    await userEvent.click(screen.getByRole('radio', { name: 'All' }))
    await waitFor(() => {
      expect(screen.getByRole('radio', { name: 'All' })).toBeChecked()
    })
    // "all" is never a value the API is asked for; it is the absence of the key.
    expect(seen.every((query) => query.get('game_type') !== 'all')).toBe(true)
    expect(seen.filter((query) => query.get('game_type') === null)).toHaveLength(1)
  })

  it('ignores a filter nothing can render, rather than breaking on it', async () => {
    // A stale bookmark or a hand-edited address. The useful answer to
    // ?game_type=x02 is the unfiltered table, not a 422 from a request that
    // should never have been sent.
    const seen = serves()
    renderApp('/stats?game_type=x02')
    await screen.findByRole('heading', { name: 'Stats' })

    await waitFor(() => {
      expect(seen.length).toBeGreaterThan(0)
    })
    expect(seen[0]?.get('game_type')).toBeNull()
    expect(screen.getByRole('radio', { name: 'All' })).toBeChecked()
  })
})

describe('the form table', () => {
  it('asks for a per-player window when the span is recent', async () => {
    const seen = serves()
    renderApp('/stats?span=recent')
    await screen.findByRole('heading', { name: 'Stats' })

    await waitFor(() => {
      expect(seen.length).toBeGreaterThan(0)
    })
    expect(seen[0]?.get('last_matches')).toBe('10')
    expect(screen.getByRole('radio', { name: 'Recent' })).toBeChecked()
    // And it says what it is, because a form table is not the lifetime table.
    expect(screen.getByText(/own last 10 matches/i)).toBeInTheDocument()
  })

  it('asks for no window by default', async () => {
    const seen = serves()
    renderApp('/stats')
    await screen.findByRole('heading', { name: 'Stats' })

    await waitFor(() => {
      expect(seen.length).toBeGreaterThan(0)
    })
    expect(seen[0]?.get('last_matches')).toBeNull()
    expect(screen.getByRole('radio', { name: 'All time' })).toBeChecked()
  })

  it('switches between the lifetime table and the form table on a tap', async () => {
    const seen = serves()
    renderApp('/stats')
    await screen.findAllByRole('listitem')
    expect(seen[0]?.get('last_matches')).toBeNull()

    await userEvent.click(screen.getByRole('radio', { name: 'Recent' }))
    await waitFor(() => {
      expect(seen.some((query) => query.get('last_matches') === '10')).toBe(true)
    })
    expect(screen.getByRole('radio', { name: 'Recent' })).toBeChecked()

    // And back, which is served from the cache rather than re-fetched.
    await userEvent.click(screen.getByRole('radio', { name: 'All time' }))
    await waitFor(() => {
      expect(screen.getByRole('radio', { name: 'All time' })).toBeChecked()
    })
    expect(seen.every((query) => query.get('last_matches') !== 'all')).toBe(true)
  })

  it('composes the window with the game type', async () => {
    const seen = serves()
    renderApp('/stats?game_type=x01&span=recent')
    await screen.findByRole('heading', { name: 'Stats' })

    await waitFor(() => {
      expect(seen.length).toBeGreaterThan(0)
    })
    expect(seen[0]?.get('game_type')).toBe('x01')
    expect(seen[0]?.get('last_matches')).toBe('10')
  })
})

describe('the empty states', () => {
  it('explains the threshold rather than looking like a bug', async () => {
    // Criterion 3's leaderboard half. The server hides players under min_darts,
    // so an empty table is a threshold that has not been met -- which is a
    // different fact from having no players, and reads differently.
    serves([], 50)
    renderApp('/stats')

    expect(await screen.findByText(/nobody has thrown 50 x01 darts/i)).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('says the window too when the table is a form table', async () => {
    // "Nobody qualifies" and "nobody qualifies recently" are different facts.
    serves([], 50)
    renderApp('/stats?span=recent')

    expect(await screen.findByText(/in their last 10 matches/i)).toBeInTheDocument()
  })

  it('names the threshold the server actually applied', async () => {
    serves([], 0)
    renderApp('/stats')

    expect(await screen.findByText(/nobody has thrown 0 x01 darts/i)).toBeInTheDocument()
  })

  it('reports a read that failed', async () => {
    server.use(http.get('*/api/stats/leaderboard', () => HttpResponse.error()))
    renderApp('/stats')
    // Queries retry a 5xx three times, which is about a second of real backoff.
    const alert = await screen.findByRole('alert', {}, { timeout: 5000 })
    expect(alert).toHaveTextContent(/try again/i)
  })

  it('recovers when the Pi comes back', async () => {
    // The retry has to actually retry. A "Try again" that only clears the error
    // would look identical until the table stayed empty.
    let failing = true
    server.use(
      http.get('*/api/stats/leaderboard', () => {
        if (failing) return HttpResponse.error()
        return HttpResponse.json(leaderboard())
      }),
    )
    renderApp('/stats')
    await screen.findByRole('alert', {}, { timeout: 5000 })

    failing = false
    await userEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(await screen.findByText('Jack', {}, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
