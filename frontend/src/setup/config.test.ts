/**
 * The setup reducer, and the claim that everything it can build is legal.
 *
 * #23 asks that "the payload posted validates server-side on the first try for
 * every reachable UI configuration", which a handful of worked examples cannot
 * discharge -- it is a statement about all of them. So the second half of this
 * file enumerates every state the screen can reach (six games, both in-rules,
 * both out-rules, every leg count, and a set of team shapes covering solo,
 * even and uneven) and checks each built body against the rules the server
 * actually enforces, transcribed from `repo/config.py` and
 * `api/matches.py::validate_composition`.
 *
 * That mirrors `tests/repo/test_config.py`, which offers the same kind of grid
 * to `GameConfig` and to a raw INSERT and asserts the two agree. It is not the
 * same as running the payloads through Pydantic -- only a backend test could
 * do that -- so what it proves is that the builder satisfies the constraints
 * as transcribed. The transcription is the part to re-read if the server's
 * rules ever move.
 *
 * The weaker half of the criterion -- that the body is shaped like
 * `MatchWrite` at all -- is discharged by the compiler rather than here:
 * `buildMatch` is declared to return the generated type, and `npm run
 * typecheck` builds this file too.
 */
import { describe, expect, it } from 'vitest'
import type { components } from '../api/schema'
import {
  buildMatch,
  GAME_OPTIONS,
  INITIAL_STATE,
  isX01,
  MAX_LEGS,
  membersOf,
  MIN_LEGS,
  reduce,
  teamOf,
  type GameId,
  type MatchWrite,
  type Rule,
  type SetupState,
  type TeamId,
} from './config'

/** Apply taps in order, from wherever. Reads like the screen being used. */
function tap(state: SetupState, ...playerIds: number[]): SetupState {
  return playerIds.reduce((acc, playerId) => reduce(acc, { type: 'tapPlayer', playerId }), state)
}

/** The teams as a shape, e.g. `['A', 'B', 'A']` for players 1, 2 and 3. */
function sides(state: SetupState): TeamId[] {
  return state.assignments.map((a) => a.team)
}

describe('the tap cycle', () => {
  it('fills the two teams alternately, so four taps are a 2v2', () => {
    const state = tap(INITIAL_STATE, 1, 2, 3, 4)

    expect(sides(state)).toEqual(['A', 'B', 'A', 'B'])
    expect(membersOf(state, 'A').map((a) => a.playerId)).toEqual([1, 3])
    expect(membersOf(state, 'B').map((a) => a.playerId)).toEqual([2, 4])
  })

  it('makes two players a 1v1, which is what "solo" means for two', () => {
    const state = tap(INITIAL_STATE, 1, 2)

    expect(teamOf(state, 1)).toBe('A')
    expect(teamOf(state, 2)).toBe('B')
  })

  it('leaves three players as a 2v1 with no further taps', () => {
    const state = tap(INITIAL_STATE, 1, 2, 3)

    expect(membersOf(state, 'A').map((a) => a.playerId)).toEqual([1, 3])
    expect(membersOf(state, 'B').map((a) => a.playerId)).toEqual([2])
  })

  it('makes five players a 3v2', () => {
    const state = tap(INITIAL_STATE, 1, 2, 3, 4, 5)

    expect(membersOf(state, 'A')).toHaveLength(3)
    expect(membersOf(state, 'B')).toHaveLength(2)
  })

  it('moves a player to the other team on a second tap, whichever side they started', () => {
    // Player 1 was filled onto A and player 2 onto B, so between them they
    // cover both directions of the swap.
    const moved = tap(INITIAL_STATE, 1, 2, 1, 2)

    expect(teamOf(moved, 1)).toBe('B')
    expect(teamOf(moved, 2)).toBe('A')
  })

  it('drops a player out on a third tap', () => {
    const state = tap(INITIAL_STATE, 1, 2, 1, 1)

    expect(teamOf(state, 1)).toBeNull()
    expect(state.assignments).toHaveLength(1)
  })

  it('gives a dropped player a fresh cycle when they are tapped back in', () => {
    // Out and back in: player 1 rejoins the smaller team, which is now A
    // again because only player 2 (on B) is left.
    const state = tap(INITIAL_STATE, 1, 2, 1, 1, 1)

    expect(teamOf(state, 1)).toBe('A')
    expect(tap(state, 1).assignments.find((a) => a.playerId === 1)?.team).toBe('B')
  })

  it('refills the gap rather than counting past it', () => {
    // 1 on A, 2 on B, 3 on A; then 3 taps out twice more and leaves.
    const afterLeaving = tap(INITIAL_STATE, 1, 2, 3, 3, 3)
    expect(sides(afterLeaving)).toEqual(['A', 'B'])

    // The next player joins B or A by which is smaller, not by tap parity.
    expect(teamOf(tap(afterLeaving, 4), 4)).toBe('A')
  })

  it('never lets one player hold two places', () => {
    const state = tap(INITIAL_STATE, 1, 1, 1, 1, 1, 1, 1)
    const ids = state.assignments.map((a) => a.playerId)

    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe('the other controls', () => {
  it('keeps the team assignment when the game type changes', () => {
    const teams = tap(INITIAL_STATE, 1, 2, 3, 4)
    const switched = reduce(teams, { type: 'game', game: 'cricket-cutthroat' })

    expect(switched.game).toBe('cricket-cutthroat')
    expect(switched.assignments).toEqual(teams.assignments)
  })

  it('keeps the x01 rules across a trip through cricket', () => {
    const chosen = reduce(reduce(INITIAL_STATE, { type: 'inRule', rule: 'double' }), {
      type: 'outRule',
      rule: 'master',
    })
    const there = reduce(chosen, { type: 'game', game: 'cricket-quick' })
    const back = reduce(there, { type: 'game', game: '301' })

    expect(back.inRule).toBe('double')
    expect(back.outRule).toBe('master')
  })

  it('clamps the leg count to the stepper bounds', () => {
    expect(reduce(INITIAL_STATE, { type: 'legsToWin', legs: 99 }).legsToWin).toBe(MAX_LEGS)
    expect(reduce(INITIAL_STATE, { type: 'legsToWin', legs: -1 }).legsToWin).toBe(MIN_LEGS)
    expect(reduce(INITIAL_STATE, { type: 'legsToWin', legs: 4 }).legsToWin).toBe(4)
  })

  it('opens on 501, straight in, double out, first to three', () => {
    expect(INITIAL_STATE).toMatchObject({
      game: '501',
      inRule: 'straight',
      outRule: 'double',
      legsToWin: 3,
      assignments: [],
    })
  })

  it('reports no team for a player nobody has tapped', () => {
    expect(teamOf(INITIAL_STATE, 7)).toBeNull()
  })
})

describe('buildMatch refuses what the server would', () => {
  it('returns null with nobody selected', () => {
    expect(buildMatch(INITIAL_STATE)).toBeNull()
  })

  it('returns null with one player, who is one team', () => {
    expect(buildMatch(tap(INITIAL_STATE, 1))).toBeNull()
  })

  it('returns null when everyone has been moved onto one team', () => {
    // 1 -> A, 2 -> B, then 2 swaps to A. Two players, one empty team.
    const lopsided = tap(INITIAL_STATE, 1, 2, 2)

    expect(membersOf(lopsided, 'B')).toHaveLength(0)
    expect(buildMatch(lopsided)).toBeNull()
  })
})

describe('buildMatch builds what the server wants', () => {
  it('posts an x01 body carrying the score and both rules and no variant', () => {
    const state = tap(INITIAL_STATE, 1, 2, 3, 4)

    expect(buildMatch(state)).toEqual({
      config: {
        game_type: 'x01',
        start_score: 501,
        in_rule: 'straight',
        out_rule: 'double',
        best_of: 5,
        start_rule: 'alternate',
        fixed_team: 0,
      },
      teams: [{ player_ids: [1, 3] }, { player_ids: [2, 4] }],
    })
  })

  it('posts a cricket body carrying the variant and none of the x01 fields', () => {
    const state = reduce(tap(INITIAL_STATE, 1, 2), { type: 'game', game: 'cricket-quick' })

    expect(buildMatch(state)).toEqual({
      config: {
        game_type: 'cricket',
        variant: 'quick',
        best_of: 5,
        start_rule: 'alternate',
        fixed_team: 0,
      },
      teams: [{ player_ids: [1] }, { player_ids: [2] }],
    })
  })

  it('turns legs to win into an odd best_of', () => {
    const pairs = [
      { legs: 1, bestOf: 1 },
      { legs: 2, bestOf: 3 },
      { legs: 3, bestOf: 5 },
      { legs: 4, bestOf: 7 },
      { legs: 5, bestOf: 9 },
    ]
    for (const { legs, bestOf } of pairs) {
      const state = reduce(tap(INITIAL_STATE, 1, 2), { type: 'legsToWin', legs })
      expect(buildMatch(state)?.config.best_of).toBe(bestOf)
    }
  })

  it('posts uneven teams as they were built', () => {
    // 2v1: the fill alone, no swapping. 1 and 3 against 2.
    expect(buildMatch(tap(INITIAL_STATE, 1, 2, 3))?.teams).toEqual([
      { player_ids: [1, 3] },
      { player_ids: [2] },
    ])

    // 2v3: five players fill to 3v2, then 5 swaps across.
    expect(buildMatch(tap(INITIAL_STATE, 1, 2, 3, 4, 5, 5))?.teams).toEqual([
      { player_ids: [1, 3] },
      { player_ids: [2, 4, 5] },
    ])
  })
})

/*
 * The enumeration.
 *
 * `GameConfig` forbids unknown keys outright (`extra="forbid"`), so an extra
 * key is a 422 rather than something quietly ignored — which makes the key
 * set worth asserting rather than just the values.
 */
const CONFIG_KEYS = new Set([
  'game_type',
  'best_of',
  'variant',
  'start_score',
  'in_rule',
  'out_rule',
  'start_rule',
  'fixed_team',
])

const START_RULES: readonly components['schemas']['StartRule'][] = [
  'alternate',
  'loser_starts',
  'winner_starts',
  'fixed',
]

const RULES: readonly Rule[] = ['straight', 'double', 'master']

/** Every sequence of taps worth reaching: solo, even, uneven, and moved. */
const TEAM_SHAPES: readonly { name: string; taps: number[] }[] = [
  { name: '1v1', taps: [1, 2] },
  { name: '2v1', taps: [1, 2, 3] },
  { name: '2v2', taps: [1, 2, 3, 4] },
  { name: '3v2', taps: [1, 2, 3, 4, 5] },
  { name: '2v3 by moving one across', taps: [1, 2, 3, 4, 5, 5] },
  { name: '1v3 by moving one across', taps: [1, 2, 3, 4, 3] },
  { name: 'a rejoin after dropping out', taps: [1, 2, 3, 3, 3, 4, 3] },
]

/** Exactly the rules `repo/config.py` and `api/matches.py` enforce. */
function assertServerWouldAccept(body: MatchWrite): void {
  const { config, teams } = body

  expect(Object.keys(config).every((key) => CONFIG_KEYS.has(key))).toBe(true)

  // repo/config.py: best_of is positive and odd.
  expect(config.best_of).toBeGreaterThan(0)
  expect(config.best_of % 2).toBe(1)

  // repo/config.py::_check_fields_match_game_type, both directions.
  if (config.game_type === 'x01') {
    expect(config.start_score ?? null).not.toBeNull()
    expect(config.start_score ?? 0).toBeGreaterThanOrEqual(1)
    expect(RULES).toContain(config.in_rule)
    expect(RULES).toContain(config.out_rule)
    expect(config.variant ?? null).toBeNull()
  } else {
    expect(config.variant ?? null).not.toBeNull()
    expect(config.start_score ?? null).toBeNull()
    expect(config.in_rule ?? null).toBeNull()
    expect(config.out_rule ?? null).toBeNull()
  }

  expect(START_RULES).toContain(config.start_rule)

  // api/matches.py: MatchWrite needs two teams, TeamWrite needs a player,
  // validate_composition needs unique ids and a fixed_team that names a team.
  expect(teams.length).toBeGreaterThanOrEqual(2)
  for (const team of teams) {
    expect(team.player_ids.length).toBeGreaterThanOrEqual(1)
    expect(team.player_ids.every((id) => Number.isInteger(id) && id > 0)).toBe(true)
  }
  const ids = teams.flatMap((team) => team.player_ids)
  expect(new Set(ids).size).toBe(ids.length)
  expect(config.fixed_team).toBeGreaterThanOrEqual(0)
  expect(config.fixed_team).toBeLessThan(teams.length)
}

describe('every reachable configuration builds a body the server accepts', () => {
  const games: GameId[] = GAME_OPTIONS.map((option) => option.value)
  const legCounts = Array.from({ length: MAX_LEGS - MIN_LEGS + 1 }, (_, i) => MIN_LEGS + i)

  it('covers all six games the picker offers', () => {
    expect(games).toHaveLength(6)
    expect(games.filter(isX01)).toHaveLength(3)
  })

  for (const shape of TEAM_SHAPES) {
    it(`accepts ${shape.name} under every game, rule and leg count`, () => {
      const withTeams = tap(INITIAL_STATE, ...shape.taps)
      let checked = 0

      for (const game of games) {
        for (const inRule of RULES) {
          for (const outRule of RULES) {
            for (const legs of legCounts) {
              const state = [
                { type: 'game', game } as const,
                { type: 'inRule', rule: inRule } as const,
                { type: 'outRule', rule: outRule } as const,
                { type: 'legsToWin', legs } as const,
              ].reduce(reduce, withTeams)

              const body = buildMatch(state)
              expect(body).not.toBeNull()
              // Narrowed by the assertion above; spelled out for the compiler.
              if (body === null) continue
              assertServerWouldAccept(body)
              checked += 1
            }
          }
        }
      }

      // 6 games x 3 in-rules x 3 out-rules x 5 leg counts.
      expect(checked).toBe(270)
    })
  }
})
