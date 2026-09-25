/**
 * The completion sheets over a real board, driven through the real play screen.
 *
 * `sheet.test.ts` enumerates the branch against payloads; this mounts it. The
 * ticket asks for "an RTL test for the leg-sheet vs match-sheet branch at the
 * best-of boundary", and the point of doing it at this level is that it also
 * proves the wiring: that both boards mount the sheet, that dismissing it
 * leaves the board behind it playable, and that the leg sheet does not come
 * back on the next dart.
 *
 * Every payload here is the one the server actually sends for that moment --
 * `active_leg` non-null for a non-deciding leg win, `is_complete` with no
 * `active_leg` for the deciding one. That asymmetry is the server's and is what
 * `sheetDue` reads; see `play/sheet.ts`.
 */
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import type { DartWrite, MatchState } from '../api/play'
import '../styles/global.css'
import { installServer, renderApp, server } from '../test-harness'
import { leg, matchState, visit, DAD, JACK } from '../play/statefixture'
import { matchStats } from '../matches/historyfixture'

installServer()

let current: MatchState
let responses: MatchState[]
let posted: DartWrite[]

function nextResponse(): MatchState {
  return responses.length > 1 ? (responses.shift() ?? current) : (responses[0] ?? current)
}

/** The stats both sheets read their per-player numbers from. */
const STATS = () =>
  matchStats(
    [
      { legId: 7, legIndex: 0, playerId: JACK, average: 57.2, dartsThrown: 15, won: true },
      { legId: 7, legIndex: 0, playerId: DAD, average: 41.9, dartsThrown: 15 },
    ],
    [
      { playerId: JACK, name: 'Jack', average: 59.3 },
      { playerId: DAD, name: 'Dad', average: 41.9 },
    ],
  )

beforeEach(() => {
  current = matchState()
  responses = [matchState()]
  posted = []
  server.use(
    http.get('*/api/matches/:matchId/state', () => HttpResponse.json(current)),
    http.post('*/api/legs/:legId/darts', async ({ request }) => {
      posted.push((await request.json()) as DartWrite)
      // Built fresh per call: a Response body reads only once, and a reused
      // module-level constant throws "body object should not be disturbed".
      return HttpResponse.json(nextResponse())
    }),
    http.post('*/api/legs/:legId/undo', () => HttpResponse.json(nextResponse())),
    http.get('*/api/stats/matches/:matchId', () => HttpResponse.json(STATS())),
  )
})

/**
 * The payload for a leg win that does not decide the match.
 *
 * `current_leg` is the leg just won, `active_leg` is the one the server opened
 * behind it. This is the only response in a match shaped this way.
 */
function legWon(options: { bestOf: number; legIndex: number; legsWon: [number, number] }) {
  const winning = visit({
    teamId: 1,
    playerId: JACK,
    scoreBefore: 40,
    scoreAfter: 0,
    labels: ['D20'],
  })
  return matchState({
    bestOf: options.bestOf,
    legIndex: options.legIndex,
    legId: 7,
    winnerTeamId: 1,
    previousVisit: winning,
    legsWon: options.legsWon,
    winner: null,
    // Leg 2 starts with the other team under `alternate`, which the server
    // decided. The sheet reports it rather than working it out.
    activeLeg: leg({
      legId: 8,
      legIndex: options.legIndex + 1,
      winnerTeamId: null,
      thrower: 1,
    }),
  })
}

/** The payload for the leg that decides the match: complete, with no next leg. */
function matchWon(options: { bestOf: number; legIndex: number; legsWon: [number, number] }) {
  return matchState({
    bestOf: options.bestOf,
    legIndex: options.legIndex,
    legId: 7,
    winnerTeamId: 1,
    legsWon: options.legsWon,
    winner: 1,
    activeLeg: null,
    activeLegId: null,
    thrower: null,
  })
}

/**
 * Throw one dart through the real keypad, waiting for the board first.
 *
 * The latch is a radio and the throw keys are buttons -- `Play.test.tsx`'s
 * `latch`/`pressKey` split. Awaiting the board rather than a score is #22's
 * lesson about stable anchors.
 *
 * The key is matched on its leading number because its accessible name carries
 * the latch and the value with it -- "20, double, 40" -- so naming it in full
 * would couple this helper to the latch it was handed.
 */
async function throwADart(name: 'Single' | 'Double' | 'Triple' = 'Double') {
  await screen.findByRole('group', { name: 'This visit' }, { timeout: 5000 })
  await userEvent.click(screen.getByRole('radio', { name }))
  await userEvent.click(screen.getByRole('button', { name: /^20,/ }))
}

describe('criterion 1: the boundary between the two sheets', () => {
  it('shows the leg sheet when a non-deciding leg is won in a best-of-3', async () => {
    responses = [legWon({ bestOf: 3, legIndex: 0, legsWon: [1, 0] })]
    renderApp('/play/42')
    await throwADart()

    const sheet = await screen.findByRole('dialog', { name: 'Leg 1 complete' })
    expect(sheet).toBeInTheDocument()
    expect(screen.getByText('Jack won the leg')).toBeInTheDocument()
    // Emphatically not the match sheet.
    expect(screen.queryByText(/won the match/)).not.toBeInTheDocument()
  })

  it('shows the match sheet when the deciding leg of a best-of-3 is won', async () => {
    responses = [matchWon({ bestOf: 3, legIndex: 1, legsWon: [2, 0] })]
    renderApp('/play/42')
    await throwADart()

    const sheet = await screen.findByRole('dialog', { name: 'Match complete' })
    expect(sheet).toBeInTheDocument()
    expect(screen.getByText('Jack won the match')).toBeInTheDocument()
    // The leg sheet must not appear, even though a leg was also just won.
    expect(screen.queryByRole('dialog', { name: /Leg \d complete/ })).not.toBeInTheDocument()
  })

  it('shows the leg sheet on leg 2 of a best-of-5, where 2 legs is not yet a win', async () => {
    // The case a naive "two legs won means the match is over" would get wrong.
    responses = [legWon({ bestOf: 5, legIndex: 1, legsWon: [2, 0] })]
    renderApp('/play/42')
    await throwADart()

    expect(await screen.findByRole('dialog', { name: 'Leg 2 complete' })).toBeInTheDocument()
    expect(screen.queryByText(/won the match/)).not.toBeInTheDocument()
  })

  it('shows the match sheet on leg 3 of a best-of-5, where 3 legs is', async () => {
    responses = [matchWon({ bestOf: 5, legIndex: 2, legsWon: [3, 0] })]
    renderApp('/play/42')
    await throwADart()

    expect(await screen.findByRole('dialog', { name: 'Match complete' })).toBeInTheDocument()
  })

  it('shows the match sheet for a best-of-1, where the first leg is the decider', async () => {
    responses = [matchWon({ bestOf: 1, legIndex: 0, legsWon: [1, 0] })]
    renderApp('/play/42')
    await throwADart()

    expect(await screen.findByRole('dialog', { name: 'Match complete' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: /Leg \d complete/ })).not.toBeInTheDocument()
  })

  it('shows no sheet at all mid-leg', async () => {
    responses = [
      matchState({ previousVisit: visit({ scoreBefore: 501, scoreAfter: 441, labels: ['T20'] }) }),
    ]
    renderApp('/play/42')
    await throwADart()

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('the leg sheet', () => {
  beforeEach(() => {
    responses = [legWon({ bestOf: 3, legIndex: 0, legsWon: [1, 0] })]
  })

  it('shows the checkout that finished it', async () => {
    renderApp('/play/42')
    await throwADart()

    await screen.findByRole('dialog', { name: 'Leg 1 complete' })
    expect(screen.getByLabelText('Checkout, D20')).toBeInTheDocument()
  })

  it('shows per-player averages for that leg, from /stats', async () => {
    renderApp('/play/42')
    await throwADart()

    await screen.findByRole('dialog', { name: 'Leg 1 complete' })
    // Per player, not per team: 57.2 is Jack's figure for leg 7 alone.
    expect(await screen.findByLabelText(/Jack, 57.2 three-dart average/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Dad, 41.9 three-dart average/)).toBeInTheDocument()
  })

  it('names who throws first in the next leg, from the server', async () => {
    renderApp('/play/42')
    await throwADart()

    await screen.findByRole('dialog', { name: 'Leg 1 complete' })
    // `alternate` gave leg 2 to Dad. The sheet reports `next_thrower` rather
    // than alternating a counter of its own.
    expect(screen.getByText(/Dad throws first in leg 2/)).toBeInTheDocument()
  })

  it('has a continue action that dismisses it and leaves the board playable', async () => {
    renderApp('/play/42')
    await throwADart()
    await screen.findByRole('dialog', { name: 'Leg 1 complete' })

    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    // The board underneath was already the new leg -- continue was a dismissal,
    // not a state change -- so no further request was made to advance it.
    expect(posted).toHaveLength(1)
    expect(screen.getByRole('button', { name: /^20,/ })).toBeEnabled()
  })

  it('stays dismissed when the next dart lands', async () => {
    // The payload for the next dart is an ordinary mid-leg one, so nothing is
    // due; the risk being guarded against is the sheet reappearing because the
    // dismissal was keyed on something that changed.
    responses = [
      legWon({ bestOf: 3, legIndex: 0, legsWon: [1, 0] }),
      matchState({ legId: 8, legIndex: 1, legsWon: [1, 0] }),
    ]
    renderApp('/play/42')
    await throwADart()
    await screen.findByRole('dialog', { name: 'Leg 1 complete' })
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

    await throwADart()

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('appears again for the *next* leg, so one dismissal is not for ever', async () => {
    responses = [
      legWon({ bestOf: 5, legIndex: 0, legsWon: [1, 0] }),
      // A second leg win, on a different leg id.
      matchState({
        bestOf: 5,
        legId: 8,
        legIndex: 1,
        winnerTeamId: 2,
        legsWon: [1, 1],
        activeLeg: leg({ legId: 9, legIndex: 2, winnerTeamId: null }),
      }),
    ]
    renderApp('/play/42')
    await throwADart()
    await screen.findByRole('dialog', { name: 'Leg 1 complete' })
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

    await throwADart()

    expect(await screen.findByRole('dialog', { name: 'Leg 2 complete' })).toBeInTheDocument()
  })

  it('can be dismissed by the scrim as well as the button', async () => {
    renderApp('/play/42')
    await throwADart()
    const sheet = await screen.findByRole('dialog', { name: 'Leg 1 complete' })
    expect(sheet).toBeInTheDocument()

    await userEvent.click(document.querySelector('.sheet__scrim')!)

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('the match sheet', () => {
  beforeEach(() => {
    responses = [matchWon({ bestOf: 3, legIndex: 1, legsWon: [2, 1] })]
  })

  it('shows the final leg tally', async () => {
    renderApp('/play/42')
    await throwADart()

    await screen.findByRole('dialog', { name: 'Match complete' })
    expect(screen.getByLabelText('Jack, 2 legs, winner')).toBeInTheDocument()
    expect(screen.getByLabelText('Dad, 1 leg')).toBeInTheDocument()
  })

  it('shows per-player match stats', async () => {
    renderApp('/play/42')
    await throwADart()

    await screen.findByRole('dialog', { name: 'Match complete' })
    // 59.3 is the match figure from the report, not the mean of the leg lines.
    expect(await screen.findByLabelText(/Jack, 59.3 three-dart average/)).toBeInTheDocument()
  })

  it('links to the dart-by-dart breakdown', async () => {
    renderApp('/play/42')
    await throwADart()

    await screen.findByRole('dialog', { name: 'Match complete' })
    expect(screen.getByRole('link', { name: /See every dart/ })).toHaveAttribute(
      'href',
      '/history/42',
    )
  })

  it('can be dismissed to look at the finished board', async () => {
    renderApp('/play/42')
    await throwADart()
    await screen.findByRole('dialog', { name: 'Match complete' })

    await userEvent.click(screen.getByRole('button', { name: 'Stay here' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    // The play screen's own end-of-match notice is still there behind it.
    expect(screen.getByText(/won the match/)).toBeInTheDocument()
  })

  it('comes back on a reload, because is_complete is durable', async () => {
    // Unlike the leg sheet. `/state` still says the match is complete, so a cold
    // mount is still a match worth announcing.
    current = matchWon({ bestOf: 3, legIndex: 1, legsWon: [2, 1] })
    renderApp('/play/42')

    expect(await screen.findByRole('dialog', { name: 'Match complete' })).toBeInTheDocument()
  })
})

describe('an abandoned match', () => {
  it('announces nothing, because there is nothing to celebrate', async () => {
    current = matchState({ status: 'abandoned', activeLegId: null, thrower: null })
    renderApp('/play/42')

    await screen.findByText(/This match was abandoned/)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('a cricket match', () => {
  it('gets the same sheet, because both boards mount it', async () => {
    const won = matchState({
      gameType: 'cricket',
      marks: [{}, {}],
      legId: 7,
      legIndex: 0,
      winnerTeamId: 1,
      legsWon: [1, 0],
      activeLeg: leg({ legId: 8, legIndex: 1, marks: [{}, {}], winnerTeamId: null, thrower: 1 }),
    })
    current = matchState({ gameType: 'cricket', marks: [{}, {}] })
    responses = [won]
    renderApp('/play/42')

    // The cricket board itself, to confirm which board is underneath.
    await screen.findByRole('table', { name: 'Cricket board' }, { timeout: 5000 })
    await userEvent.click(screen.getByRole('radio', { name: 'Triple' }))
    await userEvent.click(screen.getByRole('button', { name: /^20,/ }))

    expect(await screen.findByRole('dialog', { name: 'Leg 1 complete' })).toBeInTheDocument()
  })
})
