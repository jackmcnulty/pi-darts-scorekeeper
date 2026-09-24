/**
 * The play screen, driven the way a thumb drives it.
 *
 * Two kinds of test live here and they assert on different things. The entry
 * tests assert on the wire -- what gets posted for each key and latch -- because
 * that is #24's real claim and a screen that looks right while sending D20 for a
 * triple would pass any number of DOM assertions. The rendering tests hand the
 * screen a fixed payload and check what it draws, because the arithmetic all
 * happened on the server and what is left to get wrong is the drawing.
 *
 * What is NOT tested here, and cannot be: the 402x874 fit. jsdom does no layout,
 * so `getBoundingClientRect` is uniformly zero and any "not taller than 874px"
 * assertion passes vacuously -- against a screen 3000px tall as readily as this
 * one. `the frame refuses to scroll` below parses the stylesheet as text
 * instead, which proves the frame is built to clip rather than to scroll; it
 * does not prove the contents fit. That is verified by construction and belongs
 * to #32's device pass.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MatchState } from '../api/play'
import { ALL_KEYS, dartFor, keyLabel, MULTIPLIERS, type DartWrite } from '../play/keypad'
import { leg, matchState, pairTeams, visit } from '../play/statefixture'
import '../styles/global.css'
import { installServer, renderApp, server } from '../test-harness'

installServer()

/** The state `GET /state` answers with. Reassigned per test. */
let current: MatchState
/** Every dart body posted, in order, for asserting on the wire. */
let posted: DartWrite[]
/** Which legs an undo was aimed at, in order. */
let undone: number[]
/** What each successive POST should answer with; the last one repeats. */
let responses: MatchState[]

function nextResponse(): MatchState {
  // Shifted rather than indexed so a test that posts more darts than it queued
  // answers with the last state instead of undefined.
  return responses.length > 1 ? (responses.shift() ?? current) : (responses[0] ?? current)
}

beforeEach(() => {
  current = matchState()
  posted = []
  undone = []
  responses = [matchState()]
  server.use(
    http.get('*/api/matches/:matchId/state', () => HttpResponse.json(current)),
    http.post('*/api/legs/:legId/darts', async ({ request }) => {
      posted.push((await request.json()) as DartWrite)
      // Built fresh per call: a Response body can only be read once, and a
      // module-level constant throws "body object should not be disturbed".
      return HttpResponse.json(nextResponse())
    }),
    http.post('*/api/legs/:legId/undo', ({ params }) => {
      undone.push(Number(params.legId))
      return HttpResponse.json(nextResponse())
    }),
  )
})

/** Await the board rather than a name: a stable anchor, per #22's lesson. */
async function board() {
  return screen.findByRole('group', { name: 'This visit' })
}

function pressKey(label: string) {
  return screen.getByRole('button', { name: label })
}

function latch(name: 'Single' | 'Double' | 'Triple') {
  return screen.getByRole('radio', { name })
}

// --------------------------------------------------------------------------
// Entering darts: what reaches the wire
// --------------------------------------------------------------------------

describe('entering a dart', () => {
  it('maps all 63 keypad combinations to the payload the server accepts', async () => {
    // #24's "all 63 legal throws are reachable", discharged through the real
    // keypad rather than against the pure function alone -- so a key wired to
    // the wrong handler fails here. 23 keys x 3 latch positions is 69 taps, of
    // which the nine on 25/BULL/MISS collapse to three darts, giving 63.
    const user = userEvent.setup()
    renderApp('/play/42')
    await board()

    const expected: DartWrite[] = []
    for (const position of MULTIPLIERS) {
      for (const key of ALL_KEYS) {
        // Re-latched before every tap, because it resets to Single after each
        // dart -- which is itself the behaviour asserted below.
        if (position !== 'single')
          await user.click(latch(position === 'double' ? 'Double' : 'Triple'))
        const name = keyLabel(key, position)
        await user.click(pressKey(name))
        await waitFor(() => {
          expect(posted).toHaveLength(expected.length + 1)
        })
        expected.push({ ...dartFor(key, position), client_dart_id: posted.at(-1)!.client_dart_id })
      }
    }

    expect(posted).toHaveLength(69)
    expect(posted).toEqual(expected)

    const distinct = new Set(posted.map((d) => `${String(d.segment)}x${String(d.multiplier)}`))
    expect(distinct.size).toBe(63)
    // Every body carried its own id, which is what makes two taps two darts.
    expect(new Set(posted.map((d) => d.client_dart_id)).size).toBe(69)
  }, 60_000)

  it('posts a triple when the latch is on triple, and a single after it resets', async () => {
    const user = userEvent.setup()
    renderApp('/play/42')
    await board()

    await user.click(latch('Triple'))
    await user.click(pressKey('20, triple, 60'))
    await waitFor(() => {
      expect(posted).toHaveLength(1)
    })

    // The latch is back on Single, so the same key now means twenty.
    await user.click(pressKey('20, single'))
    await waitFor(() => {
      expect(posted).toHaveLength(2)
    })

    expect(posted.map((d) => [d.segment, d.multiplier])).toEqual([
      [20, 3],
      [20, 1],
    ])
  })

  it('returns the latch to single after each dart', async () => {
    const user = userEvent.setup()
    renderApp('/play/42')
    await board()

    await user.click(latch('Triple'))
    expect(latch('Triple')).toHaveAttribute('aria-checked', 'true')

    await user.click(pressKey('20, triple, 60'))
    await waitFor(() => {
      expect(latch('Single')).toHaveAttribute('aria-checked', 'true')
    })
    expect(latch('Triple')).toHaveAttribute('aria-checked', 'false')
  })

  it('ignores the latch on 25, BULL and MISS', async () => {
    const user = userEvent.setup()
    renderApp('/play/42')
    await board()

    for (const name of ['Outer bull, 25', 'Bull, 50', 'Miss']) {
      await user.click(latch('Triple'))
      await user.click(pressKey(name))
      await waitFor(() => {
        expect(posted).toHaveLength(posted.length)
      })
    }

    await waitFor(() => {
      expect(posted).toHaveLength(3)
    })
    // No triple bull and no multiplied miss: the three combinations the server
    // would have refused are unreachable rather than merely rejected.
    expect(posted.map((d) => [d.segment, d.multiplier])).toEqual([
      [25, 1],
      [25, 2],
      [0, 0],
    ])
  })

  it('sends one dart for a rapid double tap', async () => {
    // The criterion the ticket attributes to `client_dart_id`, which is not what
    // idempotency does: a fresh id per tap would make two taps two darts, and
    // the server would record both. So the second tap is suppressed here while
    // the first is in flight, and what is asserted is that ONE request reached
    // the server -- not merely that two ids matched.
    const user = userEvent.setup()
    let release: () => void = () => undefined
    const held = new Promise<void>((resolve_) => {
      release = resolve_
    })
    server.use(
      http.post('*/api/legs/:legId/darts', async ({ request }) => {
        posted.push((await request.json()) as DartWrite)
        await held
        return HttpResponse.json(matchState())
      }),
    )
    renderApp('/play/42')
    await board()

    const twenty = pressKey('20, single')
    await user.click(twenty)
    await waitFor(() => {
      expect(posted).toHaveLength(1)
    })
    await user.click(twenty)
    await user.click(twenty)

    release()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '20, single' })).toBeInTheDocument()
    })
    expect(posted).toHaveLength(1)
  })

  it('drops the page-zoom gesture on every key', () => {
    // The other half of the double-tap criterion, and a CSS problem rather than
    // a request one. jsdom applies the cascade, so the declaration is real even
    // though the gesture is not.
    const css = readFileSync(resolve(process.cwd(), 'src/components/Keypad.css'), 'utf8')
    expect(css).toMatch(/touch-action:\s*manipulation/)
  })
})

// --------------------------------------------------------------------------
// What the board draws
// --------------------------------------------------------------------------

describe('the scoreboard', () => {
  it('shows each team, its score, the tally and who is throwing', async () => {
    current = matchState({ remaining: [134, 301], legsWon: [1, 1], averages: [58.4, 41.2] })
    renderApp('/play/42')
    await board()

    expect(
      screen.getByRole('group', { name: 'Jack, 134 remaining, 1 leg won, throwing now' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Dad, 301 remaining, 1 leg won' })).toBeInTheDocument()
    expect(screen.getByText('Throwing')).toBeInTheDocument()
    expect(screen.getByText('Avg 58.4')).toBeInTheDocument()
  })

  it('shows the leg and match tally along the top', async () => {
    current = matchState({ startScore: 501, legIndex: 1, bestOf: 5 })
    renderApp('/play/42')
    await board()

    expect(screen.getByText('501 · Leg 2 · Best of 5')).toBeInTheDocument()
  })

  it('shows no average at all for a team that has not thrown', async () => {
    current = matchState({ averages: [null, null] })
    renderApp('/play/42')
    await board()

    // Null, not 0.0 -- which would read as a terrible average, not an absent one.
    expect(screen.queryByText(/^Avg/)).not.toBeInTheDocument()
  })

  it('fills the three dart slots as the visit is thrown', async () => {
    current = matchState({
      currentVisit: visit({
        scoreBefore: 501,
        scoreAfter: 421,
        labels: ['T20', '20'],
        isComplete: false,
      }),
    })
    renderApp('/play/42')
    const slots = await board()

    expect(within(slots).getByLabelText('Dart 1, T20')).toHaveTextContent('T20')
    expect(within(slots).getByLabelText('Dart 2, 20')).toHaveTextContent('20')
    expect(within(slots).getByLabelText('Dart 3, not thrown')).toBeInTheDocument()
  })

  it('shows 180 for a maximum visit and hands the turn over', async () => {
    // #24's first criterion. The visit total is `score_before - score_after`,
    // both of which the server sent; the thrower is `next_thrower`, likewise.
    current = matchState({
      remaining: [321, 501],
      thrower: 1,
      currentVisit: null,
      previousVisit: visit({ scoreBefore: 501, scoreAfter: 321, labels: ['T20', 'T20', 'T20'] }),
    })
    renderApp('/play/42')
    await board()

    expect(screen.getByLabelText('Visit scored 180')).toHaveTextContent('180')
    // The finished visit stays on screen, so the third dart does not vanish.
    expect(screen.getByLabelText('Dart 3, T20')).toBeInTheDocument()
    expect(
      screen.getByRole('group', { name: 'Dad, 501 remaining, 0 legs won, throwing now' }),
    ).toBeInTheDocument()
  })

  it('shows the checkout for the active player', async () => {
    current = matchState({
      remaining: [170, 501],
      checkoutPaths: [
        ['T20', 'T20', 'BULL'],
        ['T20', 'T18', 'D28'],
      ],
    })
    renderApp('/play/42')
    await board()

    // Best first, as the table stores them and the server sent them.
    expect(screen.getByText('T20 T20 BULL')).toBeInTheDocument()
  })

  it('says there is no finish for a score that cannot be checked out', async () => {
    current = matchState({ remaining: [185, 501], checkoutReason: 'not_checkable' })
    renderApp('/play/42')
    await board()

    expect(screen.getByText('No finish')).toBeInTheDocument()
  })
})

describe('a bust', () => {
  it('shows the banner, the reverted score, and the next thrower', async () => {
    // #24's second criterion. All three come off the payload: `is_bust` is the
    // verdict, `score_after == score_before` IS the revert, and `caused_bust`
    // names the dart. Nothing is recomputed here.
    current = matchState({
      remaining: [121, 501],
      thrower: 1,
      currentVisit: null,
      previousVisit: visit({
        scoreBefore: 121,
        scoreAfter: 121,
        labels: ['T20', 'T20'],
        isBust: true,
        bustAt: 1,
      }),
    })
    renderApp('/play/42')
    await board()

    expect(screen.getByRole('status')).toHaveTextContent('Bust on T20 — back to 121')
    expect(screen.getByLabelText('Visit scored 0')).toHaveTextContent('0')
    expect(
      screen.getByRole('group', { name: 'Jack, 121 remaining, 0 legs won' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('group', { name: 'Dad, 501 remaining, 0 legs won, throwing now' }),
    ).toBeInTheDocument()
  })

  it('clears the banner once the next dart lands', async () => {
    const user = userEvent.setup()
    current = matchState({
      remaining: [121, 501],
      thrower: 1,
      previousVisit: visit({
        scoreBefore: 121,
        scoreAfter: 121,
        labels: ['T20', 'T20'],
        isBust: true,
        bustAt: 1,
      }),
    })
    responses = [
      matchState({
        remaining: [121, 441],
        thrower: 1,
        currentVisit: visit({
          teamId: 2,
          scoreBefore: 501,
          scoreAfter: 441,
          labels: ['T20'],
          isComplete: false,
        }),
      }),
    ]
    renderApp('/play/42')
    await board()
    expect(screen.getByRole('status')).toHaveTextContent('Bust on T20')

    await user.click(pressKey('20, single'))

    await waitFor(() => {
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })
  })
})

describe('undo', () => {
  it('posts to the leg being played and repaints from the response', async () => {
    const user = userEvent.setup()
    current = matchState({
      legId: 7,
      dartsThrown: 1,
      remaining: [441, 501],
      currentVisit: visit({
        scoreBefore: 501,
        scoreAfter: 441,
        labels: ['T20'],
        isComplete: false,
      }),
    })
    responses = [matchState({ legId: 7, dartsThrown: 0, remaining: [501, 501] })]
    renderApp('/play/42')
    await board()

    await user.click(pressKey('UNDO'))

    await waitFor(() => {
      expect(undone).toEqual([7])
    })
    await waitFor(() => {
      expect(
        screen.getByRole('group', { name: 'Jack, 501 remaining, 0 legs won, throwing now' }),
      ).toBeInTheDocument()
    })
    expect(screen.getByLabelText('Dart 1, not thrown')).toBeInTheDocument()
  })

  it('restores the state a bust came from, including the score', async () => {
    // #24's "undo restores the previous state exactly, including across a bust".
    // The server replays the leg without the last dart; this asserts the screen
    // shows whatever that replay said rather than anything it kept itself.
    const user = userEvent.setup()
    current = matchState({
      remaining: [121, 501],
      thrower: 1,
      dartsThrown: 8,
      previousVisit: visit({
        scoreBefore: 121,
        scoreAfter: 121,
        labels: ['T20', 'T20'],
        isBust: true,
        bustAt: 1,
      }),
    })
    responses = [
      matchState({
        remaining: [61, 501],
        thrower: 0,
        dartsThrown: 7,
        checkoutPaths: [['T15', 'D8']],
        currentVisit: visit({
          scoreBefore: 121,
          scoreAfter: 61,
          labels: ['T20'],
          isComplete: false,
        }),
      }),
    ]
    renderApp('/play/42')
    await board()
    expect(screen.getByRole('status')).toHaveTextContent('Bust on T20')

    await user.click(pressKey('UNDO'))

    await waitFor(() => {
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })
    // Back to 61 with the turn returned and the hint recomputed by the server.
    expect(
      screen.getByRole('group', { name: 'Jack, 61 remaining, 0 legs won, throwing now' }),
    ).toBeInTheDocument()
    expect(screen.getByText('T15 D8')).toBeInTheDocument()
  })

  it('addresses the won leg when the next one has no darts yet', async () => {
    const user = userEvent.setup()
    current = matchState({
      legId: 7,
      dartsThrown: 9,
      winnerTeamId: 1,
      legsWon: [1, 0],
      activeLeg: leg({ legId: 8, legIndex: 1, dartsThrown: 0 }),
    })
    renderApp('/play/42')
    await board()

    await user.click(pressKey('UNDO'))

    // Leg 7, not the empty leg 8 that was opened behind it. `play.undo` reopens
    // the won leg and closes the empty one.
    await waitFor(() => {
      expect(undone).toEqual([7])
    })
  })

  it('is unavailable before anything has been thrown', async () => {
    current = matchState({ dartsThrown: 0 })
    renderApp('/play/42')
    await board()

    expect(pressKey('UNDO')).toBeDisabled()
  })

  it('can be retried when the request never got an answer', async () => {
    // Undo is not idempotent the way a dart is -- there is no key on it -- but it
    // is also not ambiguous: the server deletes the leg's *last* dart, so a
    // retry after a lost response either removes the dart the first attempt
    // already removed, or removes it now. Retrying is therefore safe, and the
    // same "Try again" serves it.
    const user = userEvent.setup()
    current = matchState({ dartsThrown: 4, legId: 7 })
    let attempt = 0
    server.use(
      http.post('*/api/legs/:legId/undo', ({ params }) => {
        undone.push(Number(params.legId))
        attempt += 1
        if (attempt === 1) return HttpResponse.error()
        return HttpResponse.json(matchState({ dartsThrown: 3, remaining: [441, 501] }))
      }),
    )
    renderApp('/play/42')
    await board()

    await user.click(pressKey('UNDO'))
    const alert = await screen.findByRole('alert', undefined, { timeout: 5000 })
    expect(alert).toHaveTextContent('Cannot reach the scoreboard')

    await user.click(within(alert).getByRole('button', { name: 'Try again' }))

    await waitFor(() => {
      expect(undone).toEqual([7, 7])
    })
    expect(
      await screen.findByRole('group', { name: 'Jack, 441 remaining, 0 legs won, throwing now' }),
    ).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Ends, refusals and the states that are not a board
// --------------------------------------------------------------------------

describe('when the match is over', () => {
  it('names the winner and refuses further darts, but still allows undo', async () => {
    const user = userEvent.setup()
    current = matchState({
      status: 'complete',
      winner: 1,
      winnerTeamId: 1,
      legsWon: [2, 0],
      dartsThrown: 9,
      thrower: null,
    })
    renderApp('/play/42')
    await board()

    expect(screen.getByRole('status')).toHaveTextContent('Jack won the match')
    await user.click(pressKey('20, single'))
    expect(posted).toHaveLength(0)
    // Undoing the dart that won it is the only way to fix a misclick, and the
    // server supports exactly that.
    expect(pressKey('UNDO')).toBeEnabled()
  })

  it('says so for an abandoned match and offers nothing to do', async () => {
    const user = userEvent.setup()
    current = matchState({ status: 'abandoned', thrower: null, dartsThrown: 6 })
    renderApp('/play/42')
    await board()

    expect(screen.getByRole('status')).toHaveTextContent('This match was abandoned')
    await user.click(pressKey('20, single'))
    expect(posted).toHaveLength(0)
    expect(pressKey('UNDO')).toBeDisabled()
  })
})

describe('a refusal from the server', () => {
  it('explains a 409 by its reason and offers no pointless retry', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('*/api/legs/:legId/darts', () =>
        HttpResponse.json(
          {
            error: {
              code: 'conflict',
              message: 'leg 7 is already won',
              detail: { reason: 'leg_complete' },
            },
          },
          { status: 409 },
        ),
      ),
    )
    renderApp('/play/42')
    await board()

    await user.click(pressKey('20, single'))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('That leg has been won already.')
    // A 4xx will fail identically forever, so there is nothing to try again.
    expect(within(alert).queryByRole('button')).not.toBeInTheDocument()
  })

  it('retries a lost dart with the same client_dart_id', async () => {
    // Where idempotency genuinely earns its keep. A dart whose request never
    // came back may well have been recorded, so the retry has to be the same
    // dart rather than a new one -- which is what makes it safe to offer.
    const user = userEvent.setup()
    let attempt = 0
    server.use(
      http.post('*/api/legs/:legId/darts', async ({ request }) => {
        posted.push((await request.json()) as DartWrite)
        attempt += 1
        if (attempt === 1) return HttpResponse.error()
        return HttpResponse.json(matchState())
      }),
    )
    renderApp('/play/42')
    await board()

    await user.click(pressKey('20, single'))
    const alert = await screen.findByRole('alert', undefined, { timeout: 5000 })
    expect(alert).toHaveTextContent('Cannot reach the scoreboard')

    await user.click(within(alert).getByRole('button', { name: 'Try again' }))

    await waitFor(() => {
      expect(posted).toHaveLength(2)
    })
    expect(posted[1]).toEqual(posted[0])
  })
})

describe('the states that are not an x01 board', () => {
  it('names #25 for a cricket match instead of drawing a board it does not own', async () => {
    // #23 will happily start a cricket match and navigate here today.
    current = matchState({ gameType: 'cricket' })
    renderApp('/play/42')

    expect(
      await screen.findByText(/cricket board is still to come in #25/, undefined, {
        timeout: 5000,
      }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '20, single' })).not.toBeInTheDocument()
  })

  it('refuses a path that is not a match without asking the server', async () => {
    renderApp('/play/nonsense')

    expect(await screen.findByText(/That is not a match/)).toBeInTheDocument()
    // No request was made: an unhandled one would have failed the test anyway,
    // but `Number('nonsense')` reaching the API would be a 422 reported as if
    // the Pi had a problem.
  })

  it('surfaces a failed read with a way to try again', async () => {
    const user = userEvent.setup()
    let reachable = false
    server.use(
      http.get('*/api/matches/:matchId/state', () =>
        reachable ? HttpResponse.json(current) : HttpResponse.error(),
      ),
    )
    renderApp('/play/42')

    // Queries retry a retryable failure three times, which is about a second of
    // real backoff -- hence the generous timeout rather than an assertion on it.
    const alert = await screen.findByRole('alert', undefined, { timeout: 5000 })
    expect(alert).toHaveTextContent('Cannot reach the scoreboard')

    reachable = true
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))

    // The Pi came back, so the board draws rather than the screen staying stuck
    // on a message about a failure that is over.
    expect(await board()).toBeInTheDocument()
  })

  it('draws a 2v2 with the thrower leading and the partner beneath', async () => {
    current = matchState({
      teams: pairTeams(),
      thrower: 0,
      throwerMember: 1,
      remaining: [134, 301],
    })
    renderApp('/play/42')
    await board()

    expect(
      screen.getByRole('group', {
        name: 'Ellie, with Jack, 134 remaining, 0 legs won, throwing now',
      }),
    ).toBeInTheDocument()
    expect(screen.getByText('Jack')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// The screen staying awake, and the frame it all sits in
// --------------------------------------------------------------------------

describe('the wake lock', () => {
  let release: ReturnType<typeof vi.fn>
  let request: ReturnType<typeof vi.fn>

  beforeEach(() => {
    release = vi.fn(() => Promise.resolve())
    request = vi.fn(() => Promise.resolve({ release, addEventListener: vi.fn() }))
    // jsdom does not implement `wakeLock`, though `lib.dom.d.ts` types it as
    // non-optional -- so this has to be installed rather than spied on.
    Object.defineProperty(navigator, 'wakeLock', { value: { request }, configurable: true })
  })

  afterEach(() => {
    Reflect.deleteProperty(navigator, 'wakeLock')
  })

  it('is held while a match is in progress', async () => {
    renderApp('/play/42')
    await board()

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith('screen')
    })
    expect(release).not.toHaveBeenCalled()
  })

  it('is released when the match ends', async () => {
    const user = userEvent.setup()
    responses = [matchState({ status: 'complete', winner: 1, winnerTeamId: 1, thrower: null })]
    renderApp('/play/42')
    await board()
    await waitFor(() => {
      expect(request).toHaveBeenCalled()
    })

    await user.click(pressKey('20, single'))

    await waitFor(() => {
      expect(release).toHaveBeenCalled()
    })
  })

  it('is released on the way out of the screen', async () => {
    const { unmount } = renderApp('/play/42')
    await board()
    await waitFor(() => {
      expect(request).toHaveBeenCalled()
    })

    unmount()

    await waitFor(() => {
      expect(release).toHaveBeenCalled()
    })
  })

  it('is never asked for when the match is already over', async () => {
    current = matchState({ status: 'complete', winner: 1, winnerTeamId: 1, thrower: null })
    renderApp('/play/42')
    await board()

    expect(request).not.toHaveBeenCalled()
  })
})

describe('the frame', () => {
  it('is built to clip rather than to scroll', () => {
    // NOT a test of the 402x874 fit, which jsdom cannot measure: it does no
    // layout, so every height assertion here would pass vacuously. What this
    // proves is that the frame is a fixed-height non-scrolling box, so an
    // overflow shows up as clipped content rather than as a quietly scrollable
    // screen. #4's mockup.css is built the same way and for the same reason. The
    // fit itself is verified by construction and checked on a device in #32.
    const css = readFileSync(resolve(process.cwd(), 'src/routes/Play.css'), 'utf8')
    const frame = /\.play \{([^}]*)\}/.exec(css)?.[1] ?? ''

    expect(frame).toMatch(/height:\s*100dvh/)
    expect(frame).toMatch(/overflow:\s*hidden/)
    // The safe-area insets, which jsdom resolves to 0 and cannot check either:
    // `env()` is unimplemented, so getComputedStyle would pass against a rule
    // with no padding at all.
    expect(frame).toMatch(/padding-top:\s*var\(--safe-top\)/)
    expect(frame).toMatch(/padding-bottom:\s*var\(--safe-bottom\)/)
  })

  it('gives the keypad the only stretching row, so extra rows cost the keys', () => {
    // This is what makes the layout survive a bust banner or an error strip
    // appearing: they take their height out of the keys, which stop at the 56px
    // touch floor, at which point the overflow is visible.
    const play = readFileSync(resolve(process.cwd(), 'src/routes/Play.css'), 'utf8')
    const keypad = readFileSync(resolve(process.cwd(), 'src/components/Keypad.css'), 'utf8')

    expect(/\.keypad__grid \{([^}]*)\}/.exec(keypad)?.[1] ?? '').toMatch(/flex:\s*1 1 auto/)
    for (const row of ['__topbar', '__cards', '__turn', '__bust', '__done', '__error']) {
      const rule = new RegExp(`\\.play${row} \\{([^}]*)\\}`).exec(play)?.[1] ?? ''
      expect(rule, `.play${row} must not stretch`).toMatch(/flex:\s*none/)
    }
  })
})
