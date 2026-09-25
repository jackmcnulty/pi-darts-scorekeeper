/**
 * What the cricket board decides, enumerated rather than sampled.
 *
 * #25 asks for "a mark-glyph rendering table test (0/1/2/3 marks x open/closed/
 * dead)", and the pattern this repo has settled on is to walk the whole
 * reachable set rather than pick three of it: #23 walked every config it could
 * build, #24 walked all 69 (key, latch) pairs. So the table below is every mark
 * count against every target against every variant, checked against the states
 * the engine can actually produce.
 *
 * Nothing here asserts a rule of cricket. The marks and the points are the
 * server's; what is under test is which glyph they draw and which cells go
 * dead, which is the only part #25 owns.
 */
import { describe, expect, it } from 'vitest'
import {
  boardView,
  cellKey,
  changesBetween,
  columnLabel,
  contextLine,
  CRICKET_TARGETS,
  MARK_GLYPHS,
  MARKS_TO_CLOSE,
  NON_TARGETS,
  showsPoints,
  targetLabel,
  variantOf,
  type Variant,
} from './cricket'
import { ALL_KEYS, NUMBER_KEYS } from './keypad'
import { matchState, pairTeams, type MatchStateOptions } from './statefixture'

const VARIANTS: Variant[] = ['standard', 'cutthroat', 'quick']

/** A cricket board with the two teams' marks stated outright. */
function board(
  jack: Record<number, number>,
  dad: Record<number, number>,
  options: MatchStateOptions = {},
) {
  const state = matchState({ gameType: 'cricket', marks: [jack, dad], ...options })
  return boardView(state, state.current_leg)
}

/** One cell, by target and column index. */
function cell(view: ReturnType<typeof board>, target: number, column: number) {
  const row = view.rows.find((r) => r.target === target)
  if (row === undefined) throw new Error(`no row for ${String(target)}`)
  const found = row.cells[column]
  if (found === undefined) throw new Error(`no column ${String(column)}`)
  return found
}

describe('the seven targets', () => {
  it('are the engine’s, in scoreboard order', () => {
    // `engine/cricket.py` declares TARGETS in exactly this order and the board
    // reads top to bottom. A board that listed them by number would put the
    // bull in the middle.
    expect(CRICKET_TARGETS).toEqual([20, 19, 18, 17, 16, 15, 25])
    expect(MARKS_TO_CLOSE).toBe(3)
  })

  it('draws a row per target, in that order, whatever the payload', () => {
    const view = board({}, {})
    expect(view.rows.map((row) => row.target)).toEqual([20, 19, 18, 17, 16, 15, 25])
    expect(view.rows.map((row) => row.label)).toEqual(['20', '19', '18', '17', '16', '15', 'Bull'])
  })

  it('calls 25 the bull and every other target its own number', () => {
    expect(targetLabel(25)).toBe('Bull')
    for (const target of [20, 19, 18, 17, 16, 15]) {
      expect(targetLabel(target)).toBe(String(target))
    }
  })
})

describe('the mark glyphs', () => {
  // The table #25 asks for: 0/1/2/3 marks against the state each produces.
  const GLYPHS = [
    { marks: 0, glyph: '·' },
    { marks: 1, glyph: '/' },
    { marks: 2, glyph: 'X' },
    { marks: 3, glyph: '⊗' },
  ]

  it('are the notation, and closed is not the same glyph as two marks', () => {
    // The whole job of the board is telling two marks from three at arm's
    // length, which a plain "X" for both would defeat. #4's mockup made the
    // same call and for the same reason.
    expect(MARK_GLYPHS).toEqual(['·', '/', 'X', '⊗'])
    expect(MARK_GLYPHS[2]).not.toBe(MARK_GLYPHS[3])
  })

  it('draws every mark count on every target, in every variant', () => {
    for (const variant of VARIANTS) {
      for (const target of CRICKET_TARGETS) {
        for (const { marks, glyph } of GLYPHS) {
          // The opponent is left on one mark so the row is never dead here;
          // dead is the next block's subject.
          const view = board({ [target]: marks }, { [target]: 1 }, { variant })
          const found = cell(view, target, 0)

          expect(found.glyph, `${String(marks)} on ${String(target)} in ${variant}`).toBe(glyph)
          expect(found.marks).toBe(marks)
          expect(found.state).toBe(marks >= MARKS_TO_CLOSE ? 'closed' : 'open')
        }
      }
    }
  })

  it('reads a target nobody has hit as nought marks, not as missing', () => {
    // The server omits a target nobody has hit rather than sending it as zero,
    // and `noUncheckedIndexedAccess` would type it `undefined` even if it did.
    const view = board({}, {})
    for (const row of view.rows) {
      for (const found of row.cells) {
        expect(found.marks).toBe(0)
        expect(found.state).toBe('open')
        expect(found.glyph).toBe('·')
      }
    }
  })
})

describe('a dead target', () => {
  it('is one closed by every team, in every column', () => {
    // #25's fourth criterion. Both teams have closed 20; neither has closed 19.
    const view = board({ 20: 3, 19: 3 }, { 20: 3, 19: 1 })

    const twenty = view.rows.find((row) => row.target === 20)
    expect(twenty?.dead).toBe(true)
    expect(twenty?.cells.map((c) => c.state)).toEqual(['dead', 'dead'])

    // Closed by one team only, so still worth throwing at.
    const nineteen = view.rows.find((row) => row.target === 19)
    expect(nineteen?.dead).toBe(false)
    expect(nineteen?.cells.map((c) => c.state)).toEqual(['closed', 'open'])
  })

  it('stays dead in every variant, because it is a drawing decision', () => {
    // Deliberately *not* the engine's question. `engine/cricket.py` asks
    // whether every *opponent* has closed the target, because surplus only pays
    // while somebody is open to score against; for two teams the two coincide.
    // Nothing here feeds a rule, so #25's wording is what gets implemented.
    for (const variant of VARIANTS) {
      const view = board({ 18: 3 }, { 18: 3 }, { variant })
      const row = view.rows.find((r) => r.target === 18)
      expect(row?.dead, variant).toBe(true)
    }
  })

  it('needs every team, not merely a majority of them', () => {
    const view = board({ 17: 3 }, { 17: 2 })
    expect(view.rows.find((row) => row.target === 17)?.dead).toBe(false)
  })

  it('implies closed, so no cell is ever dead on fewer than three marks', () => {
    for (const variant of VARIANTS) {
      for (const jack of [0, 1, 2, 3]) {
        for (const dad of [0, 1, 2, 3]) {
          const view = board({ 16: jack }, { 16: dad }, { variant })
          for (const found of view.rows.find((row) => row.target === 16)?.cells ?? []) {
            if (found.state === 'dead') expect(found.marks).toBeGreaterThanOrEqual(MARKS_TO_CLOSE)
          }
          // Dead is all-or-nothing down the row: it is a fact about the target.
          const states = new Set(
            view.rows.find((row) => row.target === 16)?.cells.map((c) => c.state === 'dead'),
          )
          expect(states.size).toBe(1)
        }
      }
    }
  })
})

describe('the points column', () => {
  it('is shown in standard and cut-throat, and hidden entirely in quick', () => {
    // #25's third criterion. Quick wastes every surplus mark, so every total is
    // zero for the whole leg -- a column of zeroes is worse than no column,
    // because it invites the player to wonder what moves it. #23 made cricket's
    // in/out controls absent rather than disabled for the same reason.
    expect(showsPoints('standard')).toBe(true)
    expect(showsPoints('cutthroat')).toBe(true)
    expect(showsPoints('quick')).toBe(false)
  })

  it('follows the variant on the board itself', () => {
    for (const variant of VARIANTS) {
      expect(board({}, {}, { variant }).showsPoints).toBe(variant !== 'quick')
    }
  })

  it('carries each team’s total from the payload, never a recomputed one', () => {
    const view = board({ 20: 3 }, {}, { variant: 'standard', points: [60, 0] })
    expect(view.columns.map((column) => column.points)).toEqual([60, 0])
  })

  it('defaults to standard when the config has no variant to read', () => {
    // `GameConfig` is one flat model, so `game_type === 'cricket'` does not
    // narrow `variant` away from null and the fallback is reachable code.
    expect(
      variantOf({ game_type: 'cricket', best_of: 3, fixed_team: 0, start_rule: 'alternate' }),
    ).toBe('standard')
  })
})

describe('the columns', () => {
  it('lead with whoever is at the oche and put the rest of the team below', () => {
    const state = matchState({
      gameType: 'cricket',
      marks: [{}, {}],
      teams: pairTeams(),
      thrower: 0,
      throwerMember: 1,
    })
    const view = boardView(state, state.current_leg)

    expect(view.columns[0]).toMatchObject({ name: 'Ellie', teammates: 'Jack', active: true })
    expect(view.columns[1]).toMatchObject({ name: 'Dad', teammates: 'Sam', active: false })
  })

  it('zips the leg tally on positionally, as `legs_won` is sent', () => {
    const view = board({}, {}, { legsWon: [2, 1] })
    expect(view.columns.map((column) => column.legsWon)).toEqual([2, 1])
  })

  it('lines each column up with its own marks, whatever order the leg lists teams in', () => {
    // `state.teams` sets the column order and the leg's teams are found by id,
    // so a payload that happened to list them the other way round still puts
    // Jack's marks in Jack's column.
    const state = matchState({ gameType: 'cricket', marks: [{ 20: 3 }, { 20: 1 }] })
    const flipped: typeof state = {
      ...state,
      current_leg: { ...state.current_leg, teams: [...state.current_leg.teams].reverse() },
    }
    const view = boardView(flipped, flipped.current_leg)

    expect(cell(view, 20, 0).marks).toBe(3)
    expect(cell(view, 20, 1).marks).toBe(1)
  })

  it('reads aloud as a sentence, because adjacent spans run together', () => {
    // "Jack41Legs 1" is what a screen reader makes of a name beside a number.
    const view = board({}, {}, { legsWon: [1, 0], points: [41, 27] })
    expect(columnLabel(view.columns[0]!, true)).toBe('Jack, 41 points, 1 leg won, throwing now')
    expect(columnLabel(view.columns[1]!, true)).toBe('Dad, 27 points, 0 legs won')
    // In quick there are no points to say, so the label does not invent any.
    expect(columnLabel(view.columns[1]!, false)).toBe('Dad, 0 legs won')
  })

  it('spells a cell out, because a grid of slashes says nothing on its own', () => {
    const view = board({ 20: 2 }, { 20: 3 })
    expect(cell(view, 20, 0).label).toBe('Jack, 20, 2 marks, open')
    expect(cell(view, 20, 1).label).toBe('Dad, 20, 3 marks, closed')
    expect(cell(board({ 25: 1 }, {}), 25, 0).label).toBe('Jack, Bull, 1 mark, open')
  })
})

describe('a payload that does not line up', () => {
  it('degrades instead of crashing, rather than trusting positional alignment', () => {
    // None of this is a response the server sends: `legs_won` is built from
    // `teams`, every team has a member, and the leg carries an entry per team.
    // But all three are positional or looked-up, and the failure mode of getting
    // one wrong is a white screen at a board -- so the fallbacks are real code
    // and this is what exercises them. `x01.test.ts` does the same for its cards.
    const state = matchState({ gameType: 'cricket', marks: [{ 20: 3 }, {}], legsWon: [1, 0] })
    const broken: typeof state = {
      ...state,
      // The second team, which is not the one at the oche -- an active team is
      // named from `next_thrower` and so survives having no members at all.
      teams: [state.teams[0]!, { ...state.teams[1]!, members: [] }],
      legs_won: [],
      current_leg: { ...state.current_leg, teams: [] },
    }
    const view = boardView(broken, broken.current_leg)

    expect(view.columns[0]).toMatchObject({ name: 'Jack', points: 0, legsWon: 0 })
    expect(view.columns[1]).toMatchObject({ name: 'Team 2', points: 0, legsWon: 0 })
    // A leg with no teams on it has no marks either, and says so as nought
    // rather than by throwing.
    expect(view.rows.every((row) => row.cells.every((c) => c.marks === 0))).toBe(true)
    // And nothing is dead, because no team has closed anything.
    expect(view.rows.some((row) => row.dead)).toBe(false)
  })

  it('calls no target dead when there are no teams to have closed it', () => {
    // `engine/cricket.py` reads an empty opponent list as vacuously dead, which
    // is right for a scoring rule and wrong for a drawing one: a board with no
    // columns has nothing finished on it.
    const state = matchState({ gameType: 'cricket', marks: [{}, {}] })
    const empty: typeof state = {
      ...state,
      teams: [],
      current_leg: { ...state.current_leg, teams: [] },
    }
    const view = boardView(empty, empty.current_leg)

    expect(view.columns).toHaveLength(0)
    expect(view.rows).toHaveLength(7)
    expect(view.rows.some((row) => row.dead)).toBe(false)
  })
})

describe('the context line', () => {
  it('names the variant, which is the only setting a cricket leg has', () => {
    for (const [variant, shown] of [
      ['standard', 'Standard'],
      ['cutthroat', 'Cut-throat'],
      ['quick', 'Quick'],
    ] as const) {
      const state = matchState({ gameType: 'cricket', marks: [{}, {}], variant, bestOf: 5 })
      expect(contextLine(state, state.current_leg)).toBe(`Cricket · ${shown} · Leg 1 · Best of 5`)
    }
  })

  it('counts legs from one, as a human does', () => {
    const state = matchState({ gameType: 'cricket', marks: [{}, {}], legIndex: 2 })
    expect(contextLine(state, state.current_leg)).toContain('Leg 3')
  })
})

describe('the dimmed keys', () => {
  it('are every board number that is not a cricket target', () => {
    // #25 dims them; it does not disable them. A dart at 12 is still a dart.
    expect([...NON_TARGETS].sort((a, b) => a - b)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14,
    ])
  })

  it('leave every target and both bulls and the miss undimmed', () => {
    for (const target of CRICKET_TARGETS) expect(NON_TARGETS.has(target)).toBe(false)
    // The miss is segment 0 and a legal entry in cricket as much as anywhere.
    expect(NON_TARGETS.has(0)).toBe(false)
  })

  it('cover exactly the numbered keys the keypad draws, and no more', () => {
    // Matched against `KeypadKey.segment`, which is what `Keypad` reads. A
    // number in here that no key carries would dim nothing.
    const segments = new Set(NUMBER_KEYS.map((key) => key.segment))
    for (const dimmed of NON_TARGETS) expect(segments.has(dimmed)).toBe(true)
    // Six of the twenty numbered keys are targets, so fourteen are dimmed.
    expect(NON_TARGETS.size).toBe(14)
    expect(ALL_KEYS.filter((key) => NON_TARGETS.has(key.segment))).toHaveLength(14)
  })
})

describe('what changed since the last payload', () => {
  it('reports nothing at all on the first one', () => {
    // A board opened mid-match would otherwise announce every number already
    // closed as closing now.
    const view = board({ 20: 3, 19: 3 }, { 20: 3 })
    expect(changesBetween(null, view).closed.size).toBe(0)
    expect(changesBetween(null, view).gained.size).toBe(0)
  })

  it('names the cell that has just closed, and only that one', () => {
    const before = board({ 20: 2, 19: 3 }, {})
    const after = board({ 20: 3, 19: 3 }, {})
    const changes = changesBetween(before, after)

    expect([...changes.closed]).toEqual([cellKey(1, 20)])
  })

  it('says nothing about a cell that was already closed and stayed closed', () => {
    const before = board({ 20: 3 }, {})
    const after = board({ 20: 3, 19: 1 }, {})
    expect(changesBetween(before, after).closed.size).toBe(0)
  })

  it('reports a close again after an undo reopened it', () => {
    // `play.undo` really does take a mark back, so a cell can close twice.
    const closed = board({ 20: 3 }, {})
    const reopened = board({ 20: 2 }, {})
    expect(changesBetween(closed, reopened).closed.size).toBe(0)
    expect([...changesBetween(reopened, closed).closed]).toEqual([cellKey(1, 20)])
  })

  it('names the opponent who gained points, not the thrower', () => {
    // #25's second criterion, which is what cut-throat is about: a dart pays
    // *opponents*. The totals are the server's; all this does is notice which
    // one moved, so the board can draw attention to it.
    const before = board({ 20: 3 }, {}, { variant: 'cutthroat', points: [0, 0] })
    const after = board({ 20: 3 }, {}, { variant: 'cutthroat', points: [0, 60] })
    const changes = changesBetween(before, after)

    expect([...changes.gained]).toEqual([2])
    expect(changes.gained.has(1)).toBe(false)
  })

  it('notices the thrower gaining in standard, where the surplus is theirs', () => {
    const before = board({ 20: 3 }, {}, { variant: 'standard', points: [0, 0] })
    const after = board({ 20: 3 }, {}, { variant: 'standard', points: [60, 0] })
    expect([...changesBetween(before, after).gained]).toEqual([1])
  })

  it('ignores a total that has not moved, and one that has gone down', () => {
    // Points going down is an undo. It is a change, but not one to celebrate.
    const high = board({}, {}, { points: [60, 0] })
    const low = board({}, {}, { points: [0, 0] })
    expect(changesBetween(low, low).gained.size).toBe(0)
    expect(changesBetween(high, low).gained.size).toBe(0)
  })

  it('keys a cell by team and target, so two teams on one row stay apart', () => {
    expect(cellKey(1, 20)).not.toBe(cellKey(2, 20))
    expect(cellKey(1, 20)).not.toBe(cellKey(1, 19))
  })
})
