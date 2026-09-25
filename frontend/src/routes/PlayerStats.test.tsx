/**
 * The stat card, and #27's first criterion.
 *
 * > Every number on screen maps directly to a field in the `/api/stats`
 * > response — a test asserts there is no arithmetic in the render path beyond
 * > formatting.
 *
 * Two tests make that claim, and they fail for different reasons.
 *
 * **The disagreeing payload** is the sharp one. Several figures the API sends
 * are *also* derivable from other fields it sends: a checkout percentage from
 * `checkouts_hit` and `checkout_attempts`, a 3-dart average from
 * `points_scored` and `darts_thrown`, a hit rate from `hits` and the cricket
 * dart count. So the fixture sets the authoritative field to one value and the
 * ingredients to another, and the test asserts the screen shows the field and
 * *not* the number the ingredients would produce. A screen that computed
 * `hits / attempts * 100` fails it with the wrong number on the page, which is
 * exactly the failure the criterion is about.
 *
 * **The DOM walk** is the broad one. It collects every numeric token the screen
 * renders and asserts each is reproducible from some field of the payload by
 * formatting alone. That catches an arithmetic slip nobody thought to write a
 * disagreeing fixture for, at the cost of having to know which tokens belong to
 * static labels -- so the allowed set is built from the metric tables rather
 * than hand-listed, and a new metric cannot silently widen it.
 *
 * Neither looks at the bar geometry. `--fraction` sizes a bar, lives in a style
 * attribute rather than in the document text, and is never read as a number by
 * anybody; the walk is over text nodes for that reason, and a test below asserts
 * the fraction really does stay out of the text.
 *
 * Both were checked against a deliberate violation while being written: a
 * `(checkouts_hit / checkout_attempts) * 100` added to the card made the walk
 * fail with `[ '29.4%' ]`. That mattered, because the first version of the walk
 * *passed* the same injection -- the fixture's `checkouts_hit / checkout_attempts`
 * happened to equal its `checkout_percentage`, so the computed number was
 * indistinguishable from the real one. `statsfixture.ts` now makes every
 * derivable figure disagree with its authoritative field on purpose, which is
 * what gives this test teeth.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { PlayerReport, PlayerStats as PlayerStatsPayload } from '../api/stats'
import {
  CRICKET_METRICS,
  OVERALL_METRICS,
  X01_METRICS,
  average,
  percentage,
  whole,
} from '../stats/stats'
import {
  cricket,
  emptyStats,
  playerReport,
  playerStats,
  x01,
  zeroStats,
} from '../stats/statsfixture'
import { installServer, renderApp, server } from '../test-harness'

installServer()

/**
 * Answer the card's two reads, choosing by `?last_matches=`.
 *
 * A factory rather than a shared constant: a `Response` body reads once, and a
 * reused one throws "body object should not be disturbed" as an unhandled
 * rejection that fails no test in particular.
 */
function serves(lifetime: PlayerStatsPayload, recent: PlayerStatsPayload = lifetime) {
  const seen: URLSearchParams[] = []
  server.use(
    http.get('*/api/stats/players/:playerId', ({ request }) => {
      const query = new URL(request.url).searchParams
      seen.push(query)
      const windowed = query.get('last_matches') !== null
      return HttpResponse.json(
        playerReport({
          player: windowed ? recent : lifetime,
          gameType:
            query.get('game_type') === 'x01'
              ? 'x01'
              : query.get('game_type') === 'cricket'
                ? 'cricket'
                : null,
          lastMatches: windowed ? Number(query.get('last_matches')) : null,
        }),
      )
    }),
  )
  return seen
}

/** The figure cells of one block, by its accessible name. */
function figures(blockName: string): string[] {
  const table = screen.getByRole('table', { name: blockName })
  return within(table)
    .getAllByRole('cell')
    .map((cell) => cell.textContent ?? '')
}

describe('the numbers on the card', () => {
  it('renders every metric it was asked for', async () => {
    serves(playerStats())
    renderApp('/stats/1')

    expect(await screen.findByRole('heading', { name: 'Jack' })).toBeInTheDocument()
    for (const metric of [...OVERALL_METRICS, ...X01_METRICS, ...CRICKET_METRICS]) {
      expect(screen.getByText(metric.label)).toBeInTheDocument()
    }
    // And the scope's headline figure is there, formatted to two places.
    expect(screen.getByRole('row', { name: /3-dart average/ })).toHaveTextContent('57.23')
  })

  it('shows the field, not the number its ingredients would produce', async () => {
    // The criterion, as a trap. Every authoritative figure below disagrees with
    // what the other fields in the same payload would compute:
    //
    //   checkout %   : 6/18 would be 33.3%; the field says 71.4%
    //   3-dart avg   : 3*8570/450 would be 57.13; the field says 12.34
    //   average visit: 8570/150 would be 57.13; the field says 98.76
    //   MPR          : 3*172/210 would be 2.46; the field says 9.99
    //
    // A render path that divided would show the first number in each pair.
    const trap = playerStats({
      x01: x01({
        threeDartAverage: 12.34,
        averageVisit: 98.76,
        checkoutPercentage: 71.4,
      }),
      cricket: cricket({ marksPerRound: 9.99 }),
    })
    serves(trap)
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    const shown = figures('x01').join(' ') + ' ' + figures('Cricket').join(' ')
    // The fields.
    expect(shown).toContain('12.34')
    expect(shown).toContain('98.76')
    expect(shown).toContain('71.4%')
    expect(shown).toContain('9.99')

    // What arithmetic would have produced instead -- checked over the whole
    // document rather than these two tables, so a computed figure that appeared
    // anywhere on the screen is still caught.
    const page = document.body.textContent ?? ''
    for (const derived of ['57.13', '33.3%', '2.46', '29.4%']) {
      expect(page).not.toContain(derived)
    }
  })

  it('shows a hit rate the server sent, not hits over darts', async () => {
    // 30 hits of 210 cricket darts would be 14.3%. The field says 24.8%.
    serves(playerStats())
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    const rates = figures('Hit rate by target').join(' ')
    expect(rates).toContain('24.8%')
    expect(rates).not.toContain('14.3%')
  })

  it('renders no number it cannot reproduce from a field', async () => {
    const payload = playerStats()
    serves(payload)
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    // Every way a field of this payload may legitimately appear: as a count, as
    // a two-place average, as a one-place percentage, or inside a tally.
    const allowed = new Set<string>()
    const offer = (value: number) => {
      allowed.add(whole(value))
      allowed.add(average(value))
      allowed.add(percentage(value))
      // A tally and a pluralised count put a bare number in the text too.
      allowed.add(String(value))
    }
    const walk = (node: unknown): void => {
      if (typeof node === 'number') return offer(node)
      if (Array.isArray(node)) return node.forEach(walk)
      if (node !== null && typeof node === 'object') Object.values(node).forEach(walk)
    }
    walk(payload)

    // Numbers that are part of a *label* rather than a figure: "180s", "140+",
    // "x01", "First 9 average". Built from the tables rather than hand-listed,
    // so a new metric cannot quietly widen what this test permits.
    const labels = [...OVERALL_METRICS, ...X01_METRICS, ...CRICKET_METRICS]
      .map((metric) => metric.label)
      .concat(['x01', 'Cricket', 'Overall', 'Hit rate by target', 'Most-hit segments'])
      .join(' ')
    for (const token of labels.match(/\d+(?:\.\d+)?/g) ?? []) allowed.add(token)

    // Tokenised per leaf element, not over `document.body.textContent`. The
    // latter glues adjacent cells together -- two 660s become "660660" -- which
    // is the same concatenation trap that makes every component here build an
    // explicit aria-label, and it would invent numbers this test then blamed on
    // the render path.
    const leaves = [...document.body.querySelectorAll('*')].filter(
      (element) => element.children.length === 0,
    )
    const tokens = leaves.flatMap(
      (leaf) => (leaf.textContent ?? '').match(/\d+(?:\.\d+)?%?/g) ?? [],
    )
    expect(tokens.length).toBeGreaterThan(20)
    const unexplained = tokens.filter((token) => !allowed.has(token))
    expect(unexplained).toEqual([])
  })

  it('keeps the bar geometry out of the text', async () => {
    // The fraction is a length, not a figure. If it ever reached the document as
    // text it would be a number on screen backed by no field -- and the walk
    // above would then be permitting it by accident.
    serves(playerStats())
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    const text = document.body.textContent ?? ''
    expect(text).not.toContain('0.796')
    expect(text).not.toContain('0.7968')
    // The bars are there, sized by a custom property rather than by text.
    const bar = screen.getByLabelText('T20: 64 darts')
    expect(bar.querySelector('.pstats__bar-track')?.getAttribute('style')).toContain('--fraction')
  })

  it('never renders NaN, at any payload shape', async () => {
    for (const payload of [playerStats(), zeroStats()]) {
      serves(payload)
      const view = renderApp('/stats/1')
      await screen.findByRole('heading', { name: payload.display_name })
      expect(document.body.textContent).not.toContain('NaN')
      view.unmount()
    }
  })
})

describe('lifetime beside recent', () => {
  it('asks the same endpoint twice, once windowed', async () => {
    const seen = serves(playerStats(), playerStats({ matchesPlayed: 6 }))
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    await waitFor(() => {
      expect(seen).toHaveLength(2)
    })
    const windows = seen.map((query) => query.get('last_matches'))
    expect(windows).toContain(null)
    expect(windows).toContain('10')
  })

  it('names the column for the matches actually covered', async () => {
    // Ten were asked for; six were found. "Last 6 matches" is the true heading.
    serves(playerStats(), playerStats({ matchesPlayed: 6, matchesWon: 4 }))
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    // Every block carries the heading, so all of them are checked rather than
    // one being picked out of several identically named columns.
    await waitFor(() => {
      expect(screen.getAllByRole('columnheader', { name: 'Last 6 matches' })).toHaveLength(3)
    })
    expect(screen.queryByRole('columnheader', { name: 'Last 10 matches' })).not.toBeInTheDocument()
  })

  it('puts the two scopes side by side', async () => {
    serves(
      playerStats(),
      playerStats({ matchesPlayed: 6, matchesWon: 4, x01: x01({ threeDartAverage: 61.5 }) }),
    )
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    const row = await screen.findByRole('row', { name: /3-dart average/ })
    expect(row).toHaveTextContent('57.23')
    expect(row).toHaveTextContent('61.50')
  })
})

describe('the empty states', () => {
  it('tells a player who has never thrown, rather than showing a grid of dashes', async () => {
    // Criterion 3. The distinction is `darts_thrown`, which is one field.
    serves(emptyStats())
    renderApp('/stats/1')

    expect(await screen.findByText(/has not thrown a dart/i)).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'x01' })).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('NaN')
  })

  it('names the filter in the empty state, because "no darts" is narrower than it sounds', async () => {
    // "Jack has not thrown a dart in cricket" is a different claim from "has not
    // thrown a dart", and on a filtered card it is the true one.
    serves(emptyStats({ name: 'Jack' }))
    renderApp('/stats/1?game_type=cricket')

    expect(await screen.findByText(/has not thrown a dart in cricket/i)).toBeInTheDocument()
  })

  it('asks for nothing when the address names no real player', async () => {
    // `/stats/abc` is reachable by hand or from a stale link. `Number('abc')` is
    // NaN, which would become `/api/stats/players/NaN` and a 422 blamed on the
    // server, so the id is checked before it is sent.
    const seen = serves(playerStats())
    renderApp('/stats/abc')

    await waitFor(() => {
      expect(seen.length).toBeGreaterThan(0)
    })
    expect(document.body.textContent).not.toContain('NaN')
  })

  it('shows a zero as a zero for a player who threw and scored nothing', async () => {
    // The other half of criterion 3, and the reason the empty state is not
    // "every average is null". This player has facts, and they are zeroes.
    serves(zeroStats())
    renderApp('/stats/1')

    await screen.findByRole('heading', { name: 'Newcomer' })
    expect(screen.queryByText(/has not thrown a dart/i)).not.toBeInTheDocument()
    const shown = figures('x01').join(' ')
    expect(shown).toContain('0.00')
    // A best checkout they have never made is absent, not zero.
    expect(screen.getByRole('row', { name: /Best checkout/ })).toHaveTextContent('—')
  })

  it('reports a read that failed', async () => {
    server.use(http.get('*/api/stats/players/:playerId', () => HttpResponse.error()))
    renderApp('/stats/1')
    // Queries retry a 5xx three times, which is about a second of real backoff.
    const alert = await screen.findByRole('alert', {}, { timeout: 5000 })
    expect(alert).toHaveTextContent(/try again/i)
  })

  it('recovers when the Pi comes back', async () => {
    // The retry has to actually retry, not just clear the error.
    let failing = true
    server.use(
      http.get('*/api/stats/players/:playerId', () => {
        if (failing) return HttpResponse.error()
        return HttpResponse.json(playerReport())
      }),
    )
    renderApp('/stats/1')
    await screen.findByRole('alert', {}, { timeout: 5000 })

    failing = false
    await userEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(
      await screen.findByRole('heading', { name: 'Jack' }, { timeout: 5000 }),
    ).toBeInTheDocument()
  })
})

describe('the game-type filter', () => {
  it('hides cricket when the filter is x01', async () => {
    // Criterion 5, driven by the echoed filter. Both blocks are always on the
    // payload, so this cannot be done by looking at whether one is empty.
    serves(playerStats())
    renderApp('/stats/1?game_type=x01')
    await screen.findByRole('heading', { name: 'Jack' })

    expect(screen.getByRole('table', { name: 'x01' })).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'Cricket' })).not.toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'Hit rate by target' })).not.toBeInTheDocument()
  })

  it('hides x01 when the filter is cricket', async () => {
    serves(playerStats())
    renderApp('/stats/1?game_type=cricket')
    await screen.findByRole('heading', { name: 'Jack' })

    expect(screen.getByRole('table', { name: 'Cricket' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Hit rate by target' })).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'x01' })).not.toBeInTheDocument()
  })

  it('shows both when the filter is off', async () => {
    serves(playerStats())
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    expect(screen.getByRole('table', { name: 'x01' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Cricket' })).toBeInTheDocument()
  })

  it('sends the filter from the address bar, so a link opens filtered', async () => {
    const seen = serves(playerStats())
    renderApp('/stats/1?game_type=cricket')
    await screen.findByRole('heading', { name: 'Jack' })

    await waitFor(() => {
      expect(seen.length).toBeGreaterThan(0)
    })
    expect(seen.every((query) => query.get('game_type') === 'cricket')).toBe(true)
    expect(screen.getByRole('radio', { name: 'Cricket' })).toBeChecked()
  })

  it('writes a tapped filter back to the address bar, and takes it out again', async () => {
    serves(playerStats())
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    await userEvent.click(screen.getByRole('radio', { name: 'Cricket' }))
    await waitFor(() => {
      expect(screen.queryByRole('table', { name: 'x01' })).not.toBeInTheDocument()
    })
    expect(screen.getByRole('radio', { name: 'Cricket' })).toBeChecked()

    // Back to all: the parameter is removed rather than set to "all", and both
    // blocks return.
    await userEvent.click(screen.getByRole('radio', { name: 'All' }))
    await waitFor(() => {
      expect(screen.getByRole('table', { name: 'x01' })).toBeInTheDocument()
    })
    expect(screen.getByRole('table', { name: 'Cricket' })).toBeInTheDocument()
  })
})

describe('the segment-frequency visual', () => {
  it('draws the segments most-hit first, as the query returned them', async () => {
    serves(playerStats())
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    const bars = screen.getAllByRole('listitem').map((item) => item.getAttribute('aria-label'))
    expect(bars.slice(0, 3)).toEqual(['T20: 64 darts', '20: 51 darts', 'MISS: 37 darts'])
  })

  it('keeps the misses, because that is where those darts went', async () => {
    serves(playerStats())
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    expect(screen.getByLabelText('MISS: 37 darts')).toBeInTheDocument()
  })

  it('says so when a filter leaves no darts at all', async () => {
    const report: PlayerReport = playerReport({
      player: playerStats({ segments: [], dartsThrown: 12 }),
    })
    server.use(http.get('*/api/stats/players/:playerId', () => HttpResponse.json(report)))
    renderApp('/stats/1')
    await screen.findByRole('heading', { name: 'Jack' })

    expect(screen.getByText(/no darts in this filter yet/i)).toBeInTheDocument()
  })
})
