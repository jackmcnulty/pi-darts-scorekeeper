/**
 * The leg-sheet vs match-sheet branch, enumerated rather than sampled.
 *
 * #26's first criterion is a boundary -- "finishing the deciding leg of a
 * best-of-N shows the match sheet, not the leg sheet" -- and a boundary is not
 * tested by picking a best-of and trying the middle of it. So this walks every
 * odd best-of the schema allows and every leg index within it, and asserts which
 * sheet is due at each. #23 walked every reachable config, #24 all 69 (key,
 * latch) pairs and #25 every mark count; this is the same habit.
 *
 * The distinction being asserted is a property of the *payload*, not of
 * arithmetic here: a non-deciding leg win carries a second leg in `active_leg`,
 * and a deciding one carries `is_complete` and no `active_leg`, because
 * `services.play` opens a next leg only when there is one to open. The tests
 * therefore build the payload the server would send for each case and check the
 * branch follows it. `sheet.test.ts` asserting against a fixture that got that
 * wrong would prove nothing, which is why `test_match_darts_api.py` and the
 * development-time run against a live server both exist.
 */
import { describe, expect, it } from 'vitest'
import { leg, matchState, soloTeams, visit, DAD, JACK } from './statefixture'
import {
  checkoutDarts,
  finishingVisit,
  legLines,
  matchLines,
  playerLineLabel,
  sheetDue,
  sheetKey,
  tally,
  winnerName,
} from './sheet'
import { matchStats } from '../matches/historyfixture'

/** Every best-of the setup screen can produce. `GameConfig` requires an odd one. */
const BEST_OF = [1, 3, 5, 7, 9, 11]

/** How many legs win a best-of-N. */
function toWin(bestOf: number): number {
  return Math.ceil(bestOf / 2)
}

describe('sheetDue across every best-of and leg index', () => {
  for (const bestOf of BEST_OF) {
    const needed = toWin(bestOf)

    it(`best-of-${String(bestOf)}: a non-deciding leg win shows the leg sheet`, () => {
      // Every leg index that can be won without deciding the match. For a
      // best-of-1 there are none, which is itself the boundary.
      const nonDeciding = Array.from({ length: needed - 1 }, (_, index) => index)
      for (const legIndex of nonDeciding) {
        const next = leg({ legId: 11 + legIndex, legIndex: legIndex + 1, winnerTeamId: null })
        const state = matchState({
          bestOf,
          // One short of winning: this leg win does not decide it.
          legsWon: [legIndex + 1, 0],
          winner: null,
          legId: 10 + legIndex,
          legIndex,
          winnerTeamId: 1,
          activeLeg: next,
        })

        const due = sheetDue(state)
        expect(due, `best-of-${String(bestOf)} leg ${String(legIndex)}`).not.toBeNull()
        expect(due?.kind).toBe('leg')
        // The sheet names the leg that was won and the leg that follows it.
        expect(due?.leg.leg_id).toBe(10 + legIndex)
        if (due?.kind === 'leg') expect(due.next.leg_id).toBe(11 + legIndex)
      }
    })

    it(`best-of-${String(bestOf)}: the deciding leg win shows the match sheet`, () => {
      const legIndex = needed - 1
      const state = matchState({
        bestOf,
        legsWon: [needed, 0],
        winner: 1,
        legIndex,
        winnerTeamId: 1,
        // The server opens no next leg for a won match, so there is no
        // `active_leg` and no `active_leg_id`.
        activeLeg: null,
        activeLegId: null,
        thrower: null,
      })

      const due = sheetDue(state)
      expect(due?.kind, `best-of-${String(bestOf)} deciding leg`).toBe('match')
    })
  }
})

describe('sheetDue on everything that is not a completion', () => {
  it('is null mid-leg, when no leg has been won', () => {
    expect(sheetDue(matchState())).toBeNull()
  })

  it('is null mid-visit', () => {
    const state = matchState({
      currentVisit: visit({
        scoreBefore: 501,
        scoreAfter: 441,
        labels: ['T20'],
        isComplete: false,
      }),
    })
    expect(sheetDue(state)).toBeNull()
  })

  it('is null when active_leg merely repeats current_leg', () => {
    // The server sends `active_leg: null` rather than repeating itself, but a
    // payload that named the same leg twice must not be read as a leg win.
    const current = leg({ legId: 7, legIndex: 0, winnerTeamId: null })
    const state = matchState({ activeLeg: current })
    expect(sheetDue(state)).toBeNull()
  })

  it('is null for an abandoned match, which announces nothing', () => {
    const state = matchState({ status: 'abandoned', activeLegId: null, thrower: null })
    expect(sheetDue(state)).toBeNull()
  })

  it('is a match sheet for a completed match on a plain reload', () => {
    // `is_complete` is durable, so the match sheet comes back after a refresh.
    // The leg sheet deliberately does not; see the module docstring.
    const state = matchState({
      legsWon: [2, 0],
      winner: 1,
      winnerTeamId: 1,
      activeLegId: null,
      thrower: null,
    })
    expect(sheetDue(state)?.kind).toBe('match')
  })
})

describe('sheetKey', () => {
  it('distinguishes one leg from the next, so a dismissal does not carry over', () => {
    const first = sheetDue(
      matchState({
        legIndex: 0,
        legId: 7,
        winnerTeamId: 1,
        legsWon: [1, 0],
        activeLeg: leg({ legId: 8, legIndex: 1, winnerTeamId: null }),
      }),
    )
    const second = sheetDue(
      matchState({
        legIndex: 1,
        legId: 8,
        winnerTeamId: 2,
        legsWon: [1, 1],
        activeLeg: leg({ legId: 9, legIndex: 2, winnerTeamId: null }),
      }),
    )
    expect(first).not.toBeNull()
    expect(second).not.toBeNull()
    expect(sheetKey(first!)).not.toBe(sheetKey(second!))
  })

  it('distinguishes a match sheet from a leg sheet on the same leg', () => {
    const asLeg = sheetDue(
      matchState({
        legId: 7,
        winnerTeamId: 1,
        legsWon: [1, 0],
        activeLeg: leg({ legId: 8, legIndex: 1, winnerTeamId: null }),
      }),
    )
    const asMatch = sheetDue(
      matchState({
        legId: 7,
        winnerTeamId: 1,
        legsWon: [2, 0],
        winner: 1,
        activeLegId: null,
        thrower: null,
      }),
    )
    expect(sheetKey(asLeg!)).not.toBe(sheetKey(asMatch!))
  })
})

describe('winnerName', () => {
  it('names a solo team after its member', () => {
    expect(winnerName(matchState(), 1)).toBe('Jack')
  })

  it('joins a pair', () => {
    const state = matchState({
      teams: [
        {
          id: 1,
          team_index: 0,
          name: null,
          is_solo: false,
          members: [
            { player_id: JACK, member_index: 0, display_name: 'Jack', is_archived: false },
            { player_id: 3, member_index: 1, display_name: 'Ellie', is_archived: false },
          ],
        },
        {
          id: 2,
          team_index: 1,
          name: null,
          is_solo: true,
          members: [{ player_id: DAD, member_index: 0, display_name: 'Dad', is_archived: false }],
        },
      ],
    })
    expect(winnerName(state, 1)).toBe('Jack & Ellie')
  })

  it('falls back rather than inventing a name for a team that is not there', () => {
    expect(winnerName(matchState(), 99)).toBe('Somebody')
    expect(winnerName(matchState(), null)).toBe('Somebody')
  })
})

describe('finishingVisit', () => {
  it('is the winning visit when the response still carries it', () => {
    const winning = visit({
      teamId: 1,
      scoreBefore: 40,
      scoreAfter: 0,
      labels: ['D20'],
    })
    const won = leg({ legId: 7, winnerTeamId: 1, previousVisit: winning })
    expect(finishingVisit(won)?.visit_id).toBe(winning.visit_id)
    expect(checkoutDarts(finishingVisit(won))).toEqual(['D20'])
  })

  it('is null when the last visit belongs to the losing team', () => {
    // What a reload produces: the leg is won, but the visit on the payload is
    // whoever threw most recently rather than whoever finished.
    const theirs = visit({ teamId: 2, scoreBefore: 120, scoreAfter: 60, labels: ['T20'] })
    const won = leg({ legId: 7, winnerTeamId: 1, previousVisit: theirs })
    expect(finishingVisit(won)).toBeNull()
    expect(checkoutDarts(null)).toEqual([])
  })

  it('is null for a leg with no visits on the payload at all', () => {
    expect(finishingVisit(leg({ winnerTeamId: 1 }))).toBeNull()
  })
})

describe('tally', () => {
  it('zips legs_won onto teams positionally and marks the winner', () => {
    const state = matchState({ legsWon: [2, 1], winner: 1 })
    expect(tally(state)).toEqual([
      { teamId: 1, name: 'Jack', legsWon: 2, isWinner: true },
      { teamId: 2, name: 'Dad', legsWon: 1, isWinner: false },
    ])
  })

  it('reads a team with no entry in the tally as nought rather than undefined', () => {
    // The server builds `legs_won` from `teams`, so a short one is not a payload
    // it sends. `noUncheckedIndexedAccess` still makes the index optional, and a
    // tally rendering "undefined legs" would be worse than one rendering nought.
    const state = matchState({ legsWon: [2, 1], winner: 1 })
    const short = { ...state, legs_won: [2] }
    expect(tally(short).map((line) => line.legsWon)).toEqual([2, 0])
  })
})

describe('legLines and matchLines', () => {
  const stats = matchStats(
    [
      { legId: 7, playerId: JACK, average: 57.2, dartsThrown: 15, won: true },
      { legId: 7, playerId: DAD, average: 41.9, dartsThrown: 15 },
      { legId: 8, playerId: JACK, average: 61.4, dartsThrown: 12 },
    ],
    [
      { playerId: JACK, name: 'Jack', average: 59.3 },
      { playerId: DAD, name: 'Dad', average: 41.9 },
    ],
  )

  it('filters leg lines to the one leg, per player', () => {
    const lines = legLines(stats, 7)
    expect(lines.map((line) => [line.name, line.average])).toEqual([
      ['Jack', 57.2],
      ['Dad', 41.9],
    ])
  })

  it('does not average the legs together for the match figure', () => {
    // Jack's two legs average 57.2 and 61.4; their mean is 59.3, but the match
    // figure has to come from the report rather than from that coincidence.
    const lines = matchLines(stats)
    expect(lines.find((line) => line.name === 'Jack')?.average).toBe(59.3)
  })

  it('is empty while the stats request is still in flight', () => {
    expect(legLines(undefined, 7)).toEqual([])
    expect(matchLines(undefined)).toEqual([])
  })

  it('names a player the payload has no name for rather than showing nothing', () => {
    const orphan = matchStats([{ legId: 7, playerId: 99 }], [])
    expect(legLines(orphan, 7)[0]?.name).toBe('Player 99')
  })
})

describe('playerLineLabel', () => {
  it('spells out a line, because adjacent spans concatenate', () => {
    expect(
      playerLineLabel({
        playerId: JACK,
        name: 'Jack',
        average: 57.25,
        dartsThrown: 15,
        marksPerRound: null,
      }),
    ).toBe('Jack, 57.3 three-dart average, 15 darts thrown')
  })

  it('says marks per round for a cricket line instead', () => {
    expect(
      playerLineLabel({
        playerId: JACK,
        name: 'Jack',
        average: null,
        dartsThrown: 24,
        marksPerRound: 2.125,
      }),
    ).toBe('Jack, 2.13 marks per round, 24 darts thrown')
  })

  it('says only the darts for a player with no figure yet', () => {
    expect(
      playerLineLabel({
        playerId: JACK,
        name: 'Jack',
        average: null,
        dartsThrown: 0,
        marksPerRound: null,
      }),
    ).toBe('Jack, 0 darts thrown')
  })
})

describe('soloTeams', () => {
  it('is the fixture both suites share', () => {
    expect(soloTeams().map((team) => team.id)).toEqual([1, 2])
  })
})
