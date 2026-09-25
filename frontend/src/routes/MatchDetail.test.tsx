/**
 * The match detail screen: the deep link, and criterion 3's bust rendering.
 *
 * The bust tests are the ones the criterion turns on, and they check three
 * separate things rather than one, because "renders busted visits struck through
 * with an explicit bust marker, making it clear those darts counted toward
 * darts-thrown but scored nothing" is three claims:
 *
 * 1. the darts are struck through -- asserted off the cascade, not off a class
 *    name, so a rule that was deleted or renamed fails here;
 * 2. there is an explicit marker, so the strike is not the only signal;
 * 3. the dart count is stated and is *not* struck through, because that is the
 *    part a strike would otherwise deny.
 *
 * The strike itself is asserted against the stylesheet as text, which is #25's
 * `rule()` pattern. jsdom does not expand the `text-decoration` shorthand into
 * `text-decoration-line`, so `getComputedStyle(...).textDecorationLine` reads
 * "none" for a rule that is plainly there -- an assertion that would fail for a
 * reason that has nothing to do with the criterion. Reading the sheet also
 * proves the strike is scoped to the darts and not to the whole row, which is
 * the part that matters most here.
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { LegHistory } from '../api/history'
import type { Match } from '../api/matches'
import '../styles/global.css'
import { installServer, renderApp, server } from '../test-harness'
import { bustedVisit, DAD, JACK, leg, match, matchHistory, visit } from '../matches/historyfixture'

installServer()

/** The stylesheet, as text: the only way jsdom can be asked about a shorthand. */
const css = () => readFileSync(resolve(process.cwd(), 'src/routes/MatchDetail.css'), 'utf8')

/**
 * Every declaration that applies to `selector`, run together.
 *
 * All the blocks, not the first: a selector styled twice would otherwise be
 * read from the wrong half. #25's `CricketBoard.test.tsx` is the precedent.
 */
const rule = (selector: string, sheet: string) =>
  [...sheet.matchAll(new RegExp(`^\\${selector}[^{]*\\{([^}]*)\\}`, 'gm'))]
    .map((match) => match[1] ?? '')
    .join('\n')

/**
 * The declarations for *exactly* `selector`, with no descendant part.
 *
 * `rule` is a prefix match, so `rule('.detail__visit--bust')` also collects
 * `.detail__visit--bust .detail__dart` -- which makes it useless for asserting
 * that the bare selector does *not* carry a declaration. This requires the
 * selector to be the whole of it.
 */
const exactRule = (selector: string, sheet: string) =>
  [...sheet.matchAll(new RegExp(`^\\${selector}\\s*\\{([^}]*)\\}`, 'gm'))]
    .map((match) => match[1] ?? '')
    .join('\n')

/** Serve both reads the screen makes, as a factory: a body reads only once. */
function serves(one: Match, legs: LegHistory[]) {
  server.use(
    http.get('*/api/matches/:matchId', ({ params }) => {
      if (String(params.matchId) !== String(one.id)) {
        return HttpResponse.json({ detail: 'no such match' }, { status: 404 })
      }
      return HttpResponse.json(one)
    }),
    http.get('*/api/matches/:matchId/darts', () => HttpResponse.json(matchHistory(legs, one.id))),
  )
}

/** A leg with one ordinary visit and one busted one, which is the case under test. */
function legWithABust(): LegHistory {
  return leg({
    legId: 7,
    legIndex: 0,
    winnerTeamId: 1,
    visits: [
      visit({
        visitIndex: 0,
        playerId: JACK,
        scoreBefore: 501,
        scoreAfter: 321,
        labels: ['T20', 'T20', 'T20'],
      }),
      visit({
        visitIndex: 1,
        teamId: 2,
        playerId: DAD,
        scoreBefore: 501,
        scoreAfter: 501,
        labels: ['MISS', 'MISS', 'MISS'],
      }),
      bustedVisit({
        visitIndex: 2,
        playerId: JACK,
        scoreBefore: 141,
        labels: ['T20', 'T20', 'T20'],
      }),
    ],
  })
}

describe('criterion 3: the dart-by-dart view and its busts', () => {
  it('marks the busted visit, and only the busted visit', async () => {
    serves(match({ id: 42 }), [legWithABust()])
    renderApp('/history/42')

    await screen.findByText('Leg 1')
    // Three visits, exactly one of which is the bust.
    expect(document.querySelectorAll('.detail__visit')).toHaveLength(3)
    expect(document.querySelectorAll('.detail__visit--bust')).toHaveLength(1)
  })

  it('strikes the busted darts through, asserted against the stylesheet', () => {
    // The declaration exists...
    expect(rule('.detail__visit--bust .detail__dart', css())).toMatch(
      /text-decoration:\s*line-through/,
    )
    // ...and is scoped to the darts. A strike on the whole row would also cross
    // out the dart count, which is the one thing the criterion needs left
    // standing, so the unscoped selector must *not* carry it.
    // Guarded: a selector that matched nothing would pass `not.toMatch`
    // vacuously, which would make this assertion worthless.
    expect(exactRule('.detail__visit--bust', css())).not.toBe('')
    expect(exactRule('.detail__visit--bust', css())).not.toMatch(/line-through/)
    expect(rule('.detail__thrown', css())).not.toMatch(/line-through/)
    // And the marker itself is not struck through either.
    expect(rule('.detail__bust', css())).not.toMatch(/line-through/)
  })

  it('renders the struck darts inside the busted row and the count outside it', async () => {
    serves(match({ id: 42 }), [legWithABust()])
    renderApp('/history/42')

    await screen.findByText('BUST')
    const busted = document.querySelector('.detail__visit--bust')!
    // The darts the rule above strikes through are these.
    expect(busted.querySelectorAll('.detail__dart')).toHaveLength(3)
    // The count is in the row but not among the darts, so the strike misses it.
    const thrown = busted.querySelector('.detail__thrown')
    expect(thrown).toHaveTextContent('3 darts, 0 scored')
    expect(thrown!.closest('.detail__visit-darts')).toBeNull()
  })

  it('shows an explicit bust marker, so the strike is not the only signal', async () => {
    serves(match({ id: 42 }), [legWithABust()])
    renderApp('/history/42')

    expect(await screen.findByText('BUST')).toBeInTheDocument()
  })

  it('states the darts thrown beside the bust, and does not strike that through', async () => {
    serves(match({ id: 42 }), [legWithABust()])
    renderApp('/history/42')

    await screen.findByText('BUST')
    // The clause the strike would otherwise contradict: these darts were thrown.
    expect(screen.getByText(/3 darts, 0 scored/)).toBeInTheDocument()
  })

  it('says "1 dart" rather than "1 darts" for a bust on the first throw', async () => {
    // A visit that busts on its opening dart: 20 left, T20 thrown, nothing
    // scored and the turn over after one.
    serves(match({ id: 42 }), [
      leg({
        legId: 7,
        winnerTeamId: null,
        visits: [bustedVisit({ scoreBefore: 20, labels: ['T20'] })],
      }),
    ])
    renderApp('/history/42')

    expect(await screen.findByText('1 dart, 0 scored')).toBeInTheDocument()
  })

  it('says the whole thing in the row label, for a screen reader', async () => {
    serves(match({ id: 42 }), [legWithABust()])
    renderApp('/history/42')

    await screen.findByText('BUST')
    const row = screen.getByRole('listitem', { name: /bust/i })
    const label = row.getAttribute('aria-label') ?? ''
    expect(label).toContain('bust')
    expect(label).toContain('scored nothing')
    expect(label).toContain('3 darts thrown')
  })

  it("counts the busted darts in the leg's darts-thrown total", async () => {
    serves(match({ id: 42 }), [legWithABust()])
    renderApp('/history/42')

    // Nine darts across three visits, three of which scored nothing. A view that
    // dropped the busted visit would say six.
    expect(await screen.findByText(/9 darts/)).toBeInTheDocument()
  })

  it('shows every visit of the leg, not just the last two', async () => {
    serves(match({ id: 42 }), [legWithABust()])
    renderApp('/history/42')

    await screen.findByText('Leg 1')
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
  })
})

describe('criterion 5: the deep link', () => {
  it('renders from the URL alone, with no state handed over', async () => {
    serves(match({ id: 42, bestOf: 5 }), [legWithABust()])
    renderApp('/history/42')

    // Everything on the header comes from the two GETs the id produced.
    expect(await screen.findByRole('heading', { name: '501 · Best of 5' })).toBeInTheDocument()
    expect(screen.getByText('Jack v Dad')).toBeInTheDocument()
  })

  it('renders the same screen again on a reload', async () => {
    serves(match({ id: 42 }), [legWithABust()])
    // A second `renderApp` is a cold mount with a fresh query client, which is
    // what a reload is: nothing survives in memory.
    const first = renderApp('/history/42')
    await screen.findByText('BUST')
    first.unmount()

    serves(match({ id: 42 }), [legWithABust()])
    renderApp('/history/42')
    expect(await screen.findByText('BUST')).toBeInTheDocument()
  })

  it('refuses a path that is not a match rather than asking about NaN', async () => {
    // No handlers installed: reaching the Pi at all would be an unhandled
    // request, which the harness fails on.
    renderApp('/history/nonsense')
    expect(await screen.findByText(/not a match/i)).toBeInTheDocument()
  })

  it('reports a match that is not there', async () => {
    serves(match({ id: 42 }), [])
    renderApp('/history/999')

    const alert = await screen.findByRole('alert', {}, { timeout: 5000 })
    expect(alert).toBeInTheDocument()
  })

  it('retries both reads when asked, since either could have been the failure', async () => {
    server.use(
      http.get('*/api/matches/:matchId/darts', () =>
        HttpResponse.json(matchHistory([legWithABust()], 42)),
      ),
      http.get('*/api/matches/:matchId', () =>
        HttpResponse.json({ detail: 'nope' }, { status: 500 }),
      ),
    )
    renderApp('/history/42')
    // Queries retry a 5xx three times, which is about a second of real backoff.
    await screen.findByRole('alert', {}, { timeout: 5000 })

    // The Pi starts answering, then the retry is pressed. Replacing the handler
    // rather than counting attempts: react-query's own three retries make an
    // attempt count a thing the test would have to predict.
    serves(match({ id: 42 }), [legWithABust()])
    await userEvent.click(screen.getByRole('button', { name: /Try again/i }))

    expect(await screen.findByText('BUST', {}, { timeout: 5000 })).toBeInTheDocument()
  })
})

describe('criterion 6: an abandoned match reads as abandoned here too', () => {
  it('says so, and still shows the darts that were thrown', async () => {
    serves(match({ id: 42, status: 'abandoned' }), [
      leg({
        legId: 7,
        winnerTeamId: null,
        visits: [visit({ scoreBefore: 501, scoreAfter: 441, labels: ['T20'] })],
      }),
    ])
    renderApp('/history/42')

    expect(await screen.findByText('Abandoned')).toBeInTheDocument()
    // Reading a match somebody walked away from is the point of keeping it.
    expect(screen.getByText('T20')).toBeInTheDocument()
    expect(screen.getByText(/Unfinished/)).toBeInTheDocument()
  })
})

describe('a cricket match', () => {
  it('calls the score points rather than a remainder', async () => {
    serves(match({ id: 42, gameType: 'cricket', variant: 'standard' }), [
      leg({
        legId: 7,
        winnerTeamId: 1,
        visits: [visit({ scoreBefore: 0, scoreAfter: 12, labels: ['T20'] })],
      }),
    ])
    renderApp('/history/42')

    expect(await screen.findByText('12 pts')).toBeInTheDocument()
    expect(screen.queryByText(/left/)).not.toBeInTheDocument()
  })
})

describe('a match with nothing in it', () => {
  it('says no darts were thrown rather than rendering an empty frame', async () => {
    serves(match({ id: 42, status: 'in_progress' }), [])
    renderApp('/history/42')

    expect(await screen.findByText(/No darts were thrown/)).toBeInTheDocument()
  })

  it('says a leg is empty when the match has one with no visits', async () => {
    serves(match({ id: 42, status: 'in_progress' }), [
      leg({ legId: 7, winnerTeamId: null, visits: [] }),
    ])
    renderApp('/history/42')

    expect(await screen.findByText(/No darts in this leg/)).toBeInTheDocument()
  })
})

describe('a multi-leg match', () => {
  it('shows each leg with its own winner and dart count', async () => {
    serves(match({ id: 42, bestOf: 3 }), [
      leg({
        legId: 7,
        legIndex: 0,
        winnerTeamId: 1,
        visits: [visit({ scoreBefore: 40, scoreAfter: 0, labels: ['D20'] })],
      }),
      leg({
        legId: 8,
        legIndex: 1,
        winnerTeamId: 2,
        visits: [
          visit({ teamId: 2, playerId: DAD, scoreBefore: 40, scoreAfter: 0, labels: ['D20'] }),
        ],
      }),
    ])
    renderApp('/history/42')

    await screen.findByText('Leg 1')
    expect(screen.getByText('Leg 2')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText(/Jack won/)).toBeInTheDocument()
    })
    expect(screen.getByText(/Dad won/)).toBeInTheDocument()
  })
})
