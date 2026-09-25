/**
 * The cricket board, driven the way a thumb drives it.
 *
 * The same two kinds of test `Play.test.tsx` runs, for the same reasons. The
 * entry tests assert on the wire -- what a tap actually posts -- because a
 * board that draws beautifully while sending the wrong dart would pass any
 * number of DOM assertions. The rendering tests hand the screen a fixed payload
 * and check what it draws, because the mark counting all happened in
 * `engine/cricket.py` and what is left to get wrong is the drawing.
 *
 * What is NOT tested here, and cannot be: the 402x874 fit and whether a human
 * perceives the closing animation. jsdom does no layout, so
 * `getBoundingClientRect` is uniformly zero and any height assertion passes
 * vacuously; `env()` is unimplemented, so the safe-area insets resolve to 0.
 * `the board's height budget` below parses the stylesheets as text, which
 * proves the rules that make the screen clip rather than scroll are present. It
 * does not prove the contents fit. That was measured in a real browser at
 * 402x781 while this was built -- the numbers are in `CricketBoard.css` -- and
 * belongs to #32's device pass.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import type { MatchState } from '../api/play'
import { CRICKET_TARGETS, type Variant } from '../play/cricket'
import { dartFor, NUMBER_KEYS, type DartWrite } from '../play/keypad'
import { matchState, pairTeams, visit, type MatchStateOptions } from '../play/statefixture'
import '../styles/global.css'
import { installServer, renderApp, server } from '../test-harness'

installServer()

let current: MatchState
let posted: DartWrite[]
let responses: MatchState[]

function nextResponse(): MatchState {
  return responses.length > 1 ? (responses.shift() ?? current) : (responses[0] ?? current)
}

/** A cricket match with the two teams' marks stated outright. */
function cricket(options: MatchStateOptions = {}): MatchState {
  return matchState({ gameType: 'cricket', marks: [{}, {}], ...options })
}

beforeEach(() => {
  current = cricket()
  posted = []
  responses = [cricket()]
  server.use(
    http.get('*/api/matches/:matchId/state', () => HttpResponse.json(current)),
    http.post('*/api/legs/:legId/darts', async ({ request }) => {
      posted.push((await request.json()) as DartWrite)
      // Built fresh per call: a Response body can only be read once, and a
      // module-level constant throws "body object should not be disturbed".
      return HttpResponse.json(nextResponse())
    }),
    http.post('*/api/legs/:legId/undo', () => HttpResponse.json(nextResponse())),
  )
})

/** The stylesheets, read as text: the only way jsdom can be asked about layout. */
const css = () => readFileSync(resolve(process.cwd(), 'src/routes/CricketBoard.css'), 'utf8')
const keypadCss = () => readFileSync(resolve(process.cwd(), 'src/components/Keypad.css'), 'utf8')
/**
 * Every declaration that applies to `selector`, run together.
 *
 * All the blocks, not the first one: `.cricket__row` is styled twice -- once in
 * the grid it shares with the header, once on its own -- and a helper that
 * stopped at the first match would read the wrong half and pass or fail for the
 * wrong reason.
 */
const rule = (selector: string, sheet: string) =>
  [...sheet.matchAll(new RegExp(`^\\${selector}[^{]*\\{([^}]*)\\}`, 'gm'))]
    .map((match) => match[1] ?? '')
    .join('\n')

/** Await the board rather than a name: a stable anchor, per #22's lesson. */
function board() {
  return screen.findByRole('table', { name: 'Cricket board' }, { timeout: 5000 })
}

/** The cell for one target and one column, found by its spelled-out label. */
function cell(name: string, target: string, marks: number, state: string) {
  return screen.getByRole('cell', {
    name: `${name}, ${target}, ${String(marks)} ${marks === 1 ? 'mark' : 'marks'}, ${state}`,
  })
}

// --------------------------------------------------------------------------
// Marks, per variant
// --------------------------------------------------------------------------

describe('the marks', () => {
  const VARIANTS: Variant[] = ['standard', 'cutthroat', 'quick']

  it('render correctly in all three variants', async () => {
    // #25's first criterion. Mark accounting is identical in all three --
    // `engine/cricket.py` says so in its module docstring -- so the board must
    // draw the same marks whatever the variant, and this is what says it does.
    for (const variant of VARIANTS) {
      current = cricket({ variant, marks: [{ 20: 3, 19: 2, 18: 1 }, { 20: 1 }] })
      const { unmount } = renderApp('/play/42')
      await board()

      expect(cell('Jack', '20', 3, 'closed')).toHaveTextContent('⊗')
      expect(cell('Jack', '19', 2, 'open')).toHaveTextContent('X')
      expect(cell('Jack', '18', 1, 'open')).toHaveTextContent('/')
      expect(cell('Jack', '17', 0, 'open')).toHaveTextContent('·')
      expect(cell('Dad', '20', 1, 'open')).toHaveTextContent('/')

      unmount()
    }
  })

  it('draws seven rows, in scoreboard order, with the bull at the bottom', async () => {
    current = cricket()
    renderApp('/play/42')
    const table = await board()

    const headers = within(table)
      .getAllByRole('rowheader')
      .map((node) => node.textContent)
    expect(headers).toEqual(['20', '19', '18', '17', '16', '15', 'Bull'])
    expect(within(table).getAllByRole('row')).toHaveLength(CRICKET_TARGETS.length)
  })

  it('renders a number closed by every team as dead in every column', async () => {
    // #25's fourth criterion. 20 is closed by both; 19 only by Jack, so his
    // stays `closed` -- there is still somebody to score against.
    current = cricket({
      marks: [
        { 20: 3, 19: 3 },
        { 20: 3, 19: 1 },
      ],
    })
    renderApp('/play/42')
    await board()

    expect(cell('Jack', '20', 3, 'dead')).toBeInTheDocument()
    expect(cell('Dad', '20', 3, 'dead')).toBeInTheDocument()
    expect(cell('Jack', '19', 3, 'closed')).toBeInTheDocument()
    expect(cell('Dad', '19', 1, 'open')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// The points column
// --------------------------------------------------------------------------

describe('the points column', () => {
  it('is shown in standard and in cut-throat', async () => {
    for (const variant of ['standard', 'cutthroat'] as const) {
      current = cricket({ variant, points: [41, 27] })
      const { unmount } = renderApp('/play/42')
      await board()

      expect(screen.getByText('41')).toBeInTheDocument()
      expect(screen.getByText('27')).toBeInTheDocument()
      // And it is said in the column's accessible name, not just drawn.
      expect(screen.getByLabelText(/^Jack, 41 points/)).toBeInTheDocument()

      unmount()
    }
  })

  it('is hidden entirely in quick, not drawn as a column of zeroes', async () => {
    // #25's third criterion. Quick wastes every surplus mark, so every total is
    // zero for the whole leg; greying it out would still invite the player to
    // wonder what moves it. #23 made cricket's in/out controls absent for the
    // same reason.
    current = cricket({ variant: 'quick', points: [0, 0] })
    renderApp('/play/42')
    await board()

    expect(screen.getByLabelText('Jack, 0 legs won, throwing now')).toBeInTheDocument()
    expect(screen.queryByLabelText(/points/)).not.toBeInTheDocument()
  })

  it('shows cut-throat points on the opponent, not on the thrower', async () => {
    // #25's second criterion. Jack is throwing and Dad is carrying the points,
    // which is exactly what cut-throat does: a dart pays the opponents. The
    // totals are the server's -- nothing here works out who should be paid.
    current = cricket({
      variant: 'cutthroat',
      thrower: 0,
      points: [0, 60],
      marks: [{ 20: 3 }, {}],
    })
    renderApp('/play/42')
    await board()

    expect(screen.getByLabelText('Jack, 0 points, 0 legs won, throwing now')).toBeInTheDocument()
    expect(screen.getByLabelText('Dad, 60 points, 0 legs won')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// Entering darts: what reaches the wire
// --------------------------------------------------------------------------

describe('entering a dart', () => {
  it('posts a dart at a non-target, which is dimmed but never disabled', async () => {
    // #25's fifth criterion and the reason this board does not use #4's
    // eight-key mockup keypad: a dart at 12 is still a dart. Every one of the
    // fourteen non-target keys is tapped here, because "still enterable" is a
    // claim about all of them and a single sample would not catch one key wired
    // to a no-op.
    const user = userEvent.setup()
    current = cricket()
    renderApp('/play/42')
    await board()

    const nonTargets = NUMBER_KEYS.filter((key) => !CRICKET_TARGETS.includes(key.segment))
    expect(nonTargets).toHaveLength(14)

    for (const key of nonTargets) {
      const button = screen.getByRole('button', { name: `${key.label}, single` })
      expect(button).toBeEnabled()
      // Dim, not disabled -- what `Keypad`'s `dimmed` prop draws.
      expect(button).toHaveAttribute('data-dimmed', 'true')
      await user.click(button)
    }

    await waitFor(() => {
      expect(posted).toHaveLength(14)
    })
    expect(posted.map((body) => body.segment)).toEqual(nonTargets.map((key) => key.segment))
    for (const body of posted) expect(body.multiplier).toBe(1)
  })

  it('leaves the six targets and both bulls undimmed', async () => {
    current = cricket()
    renderApp('/play/42')
    await board()

    for (const target of [20, 19, 18, 17, 16, 15]) {
      expect(screen.getByRole('button', { name: `${String(target)}, single` })).toHaveAttribute(
        'data-dimmed',
        'false',
      )
    }
    expect(screen.getByRole('button', { name: 'Outer bull, 25' })).toHaveAttribute(
      'data-dimmed',
      'false',
    )
    expect(screen.getByRole('button', { name: 'Bull, 50' })).toHaveAttribute('data-dimmed', 'false')
  })

  it('tells the two bulls apart, which one BULL key could not', async () => {
    // Cricket's outer bull is one mark and the inner is two. #4's mockup drew a
    // single BULL key, which cannot record the difference; the shared keypad
    // posts (25,1) and (25,2) absolutely, whatever the latch says.
    const user = userEvent.setup()
    current = cricket()
    renderApp('/play/42')
    await board()

    await user.click(screen.getByRole('button', { name: 'Outer bull, 25' }))
    await user.click(screen.getByRole('button', { name: 'Bull, 50' }))

    await waitFor(() => {
      expect(posted).toHaveLength(2)
    })
    expect(posted[0]).toMatchObject({ segment: 25, multiplier: 1 })
    expect(posted[1]).toMatchObject({ segment: 25, multiplier: 2 })
  })

  it('posts three marks for a triple, and resets the latch afterwards', async () => {
    const user = userEvent.setup()
    current = cricket()
    renderApp('/play/42')
    await board()

    await user.click(screen.getByRole('radio', { name: 'Triple' }))
    await user.click(screen.getByRole('button', { name: '20, triple, 60' }))

    await waitFor(() => {
      expect(posted).toHaveLength(1)
    })
    expect(posted[0]).toMatchObject(dartFor(NUMBER_KEYS[19]!, 'triple'))
    // Back to Single, or the next tap would silently mean something else.
    expect(screen.getByRole('radio', { name: 'Single' })).toBeChecked()
  })

  it('shows the dart in the visit, which is the only feedback a 12 gives', async () => {
    // A dart at a non-target moves no mark at all, so without this row entering
    // one would look to the player exactly like nothing happening.
    current = cricket({
      currentVisit: visit({
        scoreBefore: 0,
        scoreAfter: 0,
        labels: ['12'],
        isComplete: false,
      }),
    })
    renderApp('/play/42')
    await board()

    expect(screen.getByLabelText('Dart 1, 12')).toBeInTheDocument()
    expect(screen.getByLabelText('Dart 2, not thrown')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------
// The things that are not a board
// --------------------------------------------------------------------------

describe('the screen around the board', () => {
  it('names the variant and the leg along the top', async () => {
    current = cricket({ variant: 'cutthroat', legIndex: 1, bestOf: 5 })
    renderApp('/play/42')
    await board()

    expect(screen.getByText('Cricket · Cut-throat · Leg 2 · Best of 5')).toBeInTheDocument()
  })

  it('draws no checkout strip, because cricket has no checkout', async () => {
    // `hints.for_leg` returns `not_x01` with empty paths for every cricket leg.
    current = cricket()
    renderApp('/play/42')
    await board()

    expect(screen.queryByText('Checkout')).not.toBeInTheDocument()
  })

  it('leads a pair with whoever is at the oche', async () => {
    current = cricket({ teams: pairTeams(), thrower: 1, throwerMember: 1 })
    renderApp('/play/42')
    await board()

    expect(screen.getByLabelText(/^Sam, with Dad/)).toBeInTheDocument()
  })

  it('says who won and stops taking darts, without an end-of-leg flow', async () => {
    // #26 owns the interstitial. Until then the win shows in words and the
    // board rolls on, which is what #24 does and what this inherits.
    const user = userEvent.setup()
    current = cricket({ status: 'complete', winner: 2, legsWon: [0, 2], dartsThrown: 9 })
    renderApp('/play/42')
    await board()

    expect(screen.getByRole('status')).toHaveTextContent('Dad won the match.')

    await user.click(screen.getByRole('button', { name: '20, single' }))
    expect(posted).toHaveLength(0)
    // Undo still works: it is the only way to take back a mis-entered winner.
    expect(screen.getByRole('button', { name: 'UNDO' })).toBeEnabled()
  })

  it('says so for an abandoned match, and refuses both a dart and an undo', async () => {
    // #18 withholds `active_leg_id` and the thrower for an abandoned match, so
    // there is nothing to throw at and nothing to take back.
    const user = userEvent.setup()
    current = cricket({ status: 'abandoned', thrower: null, dartsThrown: 6 })
    renderApp('/play/42')
    await board()

    expect(screen.getByRole('status')).toHaveTextContent('This match was abandoned.')
    await user.click(screen.getByRole('button', { name: '20, single' }))
    expect(posted).toHaveLength(0)
    expect(screen.getByRole('button', { name: 'UNDO' })).toBeDisabled()
  })

  it('names somebody rather than nobody if the winner is not a team on screen', async () => {
    // Not a response the server sends -- the winner is always one of the teams
    // -- but the fallback is real code and a white screen at a board is the
    // failure mode it exists to prevent.
    current = cricket({ status: 'complete', winner: 99, dartsThrown: 9 })
    renderApp('/play/42')
    await board()

    expect(screen.getByRole('status')).toHaveTextContent('Somebody won the match.')
  })

  it('undoes against the leg with the darts in it', async () => {
    const user = userEvent.setup()
    const undone: number[] = []
    current = cricket({ legId: 7, dartsThrown: 4, marks: [{ 20: 1 }, {}] })
    server.use(
      http.post('*/api/legs/:legId/undo', ({ params }) => {
        undone.push(Number(params.legId))
        return HttpResponse.json(cricket({ legId: 7, dartsThrown: 3 }))
      }),
    )
    renderApp('/play/42')
    await board()

    await user.click(screen.getByRole('button', { name: 'UNDO' }))

    await waitFor(() => {
      expect(undone).toEqual([7])
    })
  })

  it('offers nothing to undo before the first dart of a leg', async () => {
    current = cricket({ dartsThrown: 0 })
    renderApp('/play/42')
    await board()

    expect(screen.getByRole('button', { name: 'UNDO' })).toBeDisabled()
  })

  it('offers a retry for a failed undo too, aimed at the same leg', async () => {
    const user = userEvent.setup()
    const undone: number[] = []
    current = cricket({ legId: 7, dartsThrown: 4 })
    let attempts = 0
    server.use(
      http.post('*/api/legs/:legId/undo', ({ params }) => {
        undone.push(Number(params.legId))
        attempts += 1
        if (attempts === 1) return HttpResponse.error()
        return HttpResponse.json(cricket({ legId: 7, dartsThrown: 3 }))
      }),
    )
    renderApp('/play/42')
    await board()

    await user.click(screen.getByRole('button', { name: 'UNDO' }))
    await user.click(await screen.findByRole('button', { name: 'Try again' }))

    await waitFor(() => {
      expect(undone).toEqual([7, 7])
    })
  })

  it('offers a retry that re-sends the identical body, id and all', async () => {
    // Mutations never retry automatically -- a dart whose request timed out may
    // well have been recorded. `client_dart_id` is what makes the manual retry
    // safe, so the retry must send the same one.
    const user = userEvent.setup()
    current = cricket()
    let attempts = 0
    server.use(
      http.post('*/api/legs/:legId/darts', async ({ request }) => {
        posted.push((await request.json()) as DartWrite)
        attempts += 1
        if (attempts === 1) return HttpResponse.error()
        return HttpResponse.json(cricket())
      }),
    )
    renderApp('/play/42')
    await board()

    await user.click(screen.getByRole('button', { name: '20, single' }))
    const retry = await screen.findByRole('button', { name: 'Try again' })
    await user.click(retry)

    await waitFor(() => {
      expect(posted).toHaveLength(2)
    })
    expect(posted[0]).toEqual(posted[1])
  })
})

// --------------------------------------------------------------------------
// The layout, as far as a stylesheet can be read
// --------------------------------------------------------------------------

describe('the board’s height budget', () => {
  it('gives the board the only stretching row, and pins the keypad', () => {
    // NOT a test of the 402x874 fit, which jsdom cannot measure. What this
    // proves is the shape that makes a banner cost height somewhere harmless.
    //
    // It is the opposite of the x01 board, and the inversion is load-bearing:
    // there the keypad grid grows and every other row is fixed, here the grid
    // is pinned to its 56px-per-key minimum and the board absorbs the slack.
    // The two cannot both grow -- they are siblings in one flex column, and
    // when both do, the keypad's larger basis starves the board into
    // overlapping rows. That was the first thing a real browser caught.
    const sheet = css()

    expect(rule('.cricket__board', sheet)).toMatch(/flex:\s*1 1 auto/)
    expect(rule('.cricket__board', sheet)).toMatch(/min-height:\s*0/)
    expect(rule('.cricket .keypad__grid', sheet)).toMatch(/flex:\s*0 0 auto/)

    for (const row of ['__header', '__visit', '__done', '__error']) {
      expect(rule(`.cricket${row}`, sheet), `.cricket${row} must not stretch`).toMatch(
        /flex:\s*none/,
      )
    }
  })

  it('lets the mark rows compress, so the keypad keeps its touch floor', () => {
    // The rows carry no hard `min-height` on purpose: they are what gives way
    // under a banner. The keys are the thing that must not, and they are held
    // at `--touch-min` by the keypad's own `grid-auto-rows`.
    expect(rule('.cricket__row', css())).toMatch(/min-height:\s*0/)
    expect(rule('.keypad__grid', keypadCss())).toMatch(
      /grid-auto-rows:\s*minmax\(var\(--touch-min\)/,
    )
  })

  it('places the spine explicitly, rather than trusting auto-placement', () => {
    // Every item pinned to row 1. The spine leads in the DOM for a screen
    // reader and sits in column 2 on screen, so without this the first mark
    // cell is placed behind it and sparse auto-flow pushes it to a second row
    // -- which silently doubles the height of all seven. Also caught in a
    // browser, and invisible to every other test here.
    expect(rule('.cricket__header > \\*,\n.cricket__row > \\*', css())).toMatch(/grid-row:\s*1/)
  })

  it('keeps the dimmed keys recessed rather than faded, so they read as live', () => {
    // A dart at 12 has to look enterable. Opacity alone would read as disabled,
    // which is the one thing #25 is explicit that these keys are not.
    const keypad = keypadCss()
    const dimmed = /\[data-dimmed='true'\][^{]*\{([^}]*)\}/.exec(keypad)?.[1] ?? ''

    expect(dimmed).toMatch(/background:/)
    expect(dimmed).not.toMatch(/opacity/)
  })
})

describe('signalling a change', () => {
  it('declares an animation for a number closing and for points landing', () => {
    // #25's sixth criterion. That a human *perceives* the change is not
    // something jsdom can be asked; what can be checked is that the rules are
    // there, that they are keyed off the state the board actually sets, and
    // that the cell says what it is in more than colour. The animation was
    // watched in a real browser while this was built.
    const sheet = css()

    expect(sheet).toMatch(/@keyframes cricket-closing/)
    expect(sheet).toMatch(/\.cricket__marks\[data-closing='true'\][^{]*\{[^}]*animation:/)
    expect(sheet).toMatch(/@keyframes cricket-gained/)
    expect(sheet).toMatch(/\.cricket__head-points--gained[^{]*\{[^}]*animation:/)
  })

  it('honours a player who has asked for less motion', () => {
    expect(css()).toMatch(/prefers-reduced-motion: reduce/)
  })

  it('marks the cell that has just closed, and nothing else', async () => {
    // The signal is driven by a diff of two payloads, so it needs two: a board
    // that arrives already closed announces nothing.
    const user = userEvent.setup()
    current = cricket({ marks: [{ 20: 2 }, {}] })
    responses = [cricket({ marks: [{ 20: 3 }, {}], dartsThrown: 1 })]
    renderApp('/play/42')
    await board()

    expect(cell('Jack', '20', 2, 'open')).toHaveAttribute('data-closing', 'false')

    await user.click(screen.getByRole('button', { name: '20, single' }))

    const closed = await screen.findByRole('cell', { name: 'Jack, 20, 3 marks, closed' })
    expect(closed).toHaveAttribute('data-closing', 'true')
    // The row below it did not close, and does not claim to have.
    expect(cell('Jack', '19', 0, 'open')).toHaveAttribute('data-closing', 'false')
  })

  it('flashes the opponent’s total after a cut-throat dart, not the thrower’s', async () => {
    // #25's second criterion, as the player sees it. Jack throws; the server
    // comes back with the 60 on Dad. The thrower's total has not moved and is
    // not marked as having moved, which is the whole point of cut-throat.
    const user = userEvent.setup()
    current = cricket({ variant: 'cutthroat', marks: [{ 20: 3 }, {}], points: [0, 0] })
    responses = [
      cricket({ variant: 'cutthroat', marks: [{ 20: 3 }, {}], points: [0, 60], dartsThrown: 4 }),
    ]
    renderApp('/play/42')
    await board()

    await user.click(screen.getByRole('button', { name: '20, single' }))

    const dad = await screen.findByText('60')
    expect(dad).toHaveClass('cricket__head-points--gained')
    // Scoped to Jack's own column: "0" appears in plenty of other places.
    const jack = screen.getByLabelText(/^Jack, 0 points/)
    expect(within(jack).getByText('0')).not.toHaveClass('cricket__head-points--gained')
  })

  it('says nothing on the first payload, however much is already closed', async () => {
    // A board opened mid-match would otherwise announce every closed number at
    // once, which is a lot of animation for nothing having happened.
    current = cricket({ marks: [{ 20: 3, 19: 3, 18: 3 }, { 20: 3 }] })
    renderApp('/play/42')
    const table = await board()

    for (const found of within(table).getAllByRole('cell')) {
      expect(found).toHaveAttribute('data-closing', 'false')
    }
  })
})
