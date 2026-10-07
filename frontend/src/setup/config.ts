/**
 * Everything the setup screen decides, and the one function that turns it into
 * a request body.
 *
 * The screen holds no derived state. It holds a `SetupState`, and every
 * question it needs answering -- which team is this player on, is the start
 * button live, what gets posted -- is a pure function of that state, in this
 * file. That is what makes #23's "the payload validates on the first try for
 * every reachable UI configuration" a thing a test can actually discharge:
 * `config.test.ts` enumerates the reachable states and checks the payloads,
 * rather than clicking through a handful of them and hoping.
 *
 * `buildMatch` returns the generated `MatchWrite` or `null`, and `null` means
 * exactly "this state is not startable". The disabled state of the start
 * button and the shape of the payload are therefore the same decision made
 * once, and cannot drift apart.
 *
 * This is a `.ts` and not part of `Setup.tsx` because ESLint's
 * `react-refresh/only-export-components` fails a file that exports both a
 * component and something else -- the same reason `api/connection.ts` sits
 * beside `components/ConnectionToast.tsx`.
 */
import type { components } from '../api/schema'

export type MatchWrite = components['schemas']['MatchWrite']
export type GameConfig = components['schemas']['GameConfig']
export type Rule = components['schemas']['Rule']

/** The six games #23's picker offers, as one flat choice. */
export type GameId =
  '301' | '501' | '701' | 'cricket-standard' | 'cricket-cutthroat' | 'cricket-quick'

/**
 * What each choice contributes to the config; the controls supply the rest.
 *
 * A `Record` rather than a list searched by id: `GAME_CONFIG[game]` is a
 * `GameConfig` fragment and never `undefined`, so nothing downstream needs a
 * branch for a game that cannot exist.
 *
 * `start_score` appears only for x01 and `variant` only for cricket, because
 * `GameConfig` rejects either on the wrong game type -- the server's
 * `_check_fields_match_game_type` mirrors the big CHECK in `0001_init.sql`.
 */
const GAME_CONFIG: Record<GameId, Pick<GameConfig, 'game_type' | 'start_score' | 'variant'>> = {
  '301': { game_type: 'x01', start_score: 301 },
  '501': { game_type: 'x01', start_score: 501 },
  '701': { game_type: 'x01', start_score: 701 },
  'cricket-standard': { game_type: 'cricket', variant: 'standard' },
  'cricket-cutthroat': { game_type: 'cricket', variant: 'cutthroat' },
  'cricket-quick': { game_type: 'cricket', variant: 'quick' },
}

export interface GameOption {
  value: GameId
  /** What the chip shows. Short, because six of these share one row. */
  label: string
  /** What it reads as. "Quick" alone does not say quick what. */
  name: string
}

/** The picker, in #23's order. 501 first among equals -- it is the default. */
export const GAME_OPTIONS: readonly GameOption[] = [
  { value: '301', label: '301', name: '301' },
  { value: '501', label: '501', name: '501' },
  { value: '701', label: '701', name: '701' },
  { value: 'cricket-standard', label: 'Cricket', name: 'Cricket' },
  { value: 'cricket-cutthroat', label: 'Cut-throat', name: 'Cut-throat cricket' },
  { value: 'cricket-quick', label: 'Quick', name: 'Quick cricket' },
]

export function isX01(game: GameId): boolean {
  return GAME_CONFIG[game].game_type === 'x01'
}

/**
 * Two teams, named rather than numbered.
 *
 * #23 is about 1v1, 2v2 and the uneven shapes in between, all of which are two
 * teams. The server is happy with more and the engine rotates through any
 * number, but a team count control is not in the ticket and a third bucket
 * nobody asked for would cost a tap on the way to every match that does not
 * want it. `A` and `B` rather than `0` and `1` so that a bug that mixes up a
 * team with an array index cannot typecheck.
 */
export type TeamId = 'A' | 'B'

export const TEAM_IDS: readonly TeamId[] = ['A', 'B']

function other(team: TeamId): TeamId {
  return team === 'A' ? 'B' : 'A'
}

/**
 * Who opens each leg, as one flat choice (#59).
 *
 * The engine has four rules and `fixed` needs a team, which with two teams is
 * five things a player can mean. They are offered as five chips rather than a
 * four-way rule control plus a team control shown under `fixed`, for the same
 * reason the game picker is flat: every choice is one tap, and the default
 * (`alternate`, as before #59) is none. Measured at 402x781 in a browser, a
 * four-way segmented control wraps "Loser starts" and "Winner starts" onto two
 * lines; five chips wrap onto three rows with every label on one. Jack chose
 * this shape on #59 over the ticket's literal four-plus-a-picker.
 *
 * Flat is also what keeps `fixed_team` honest. `rotation.starting_team` reads
 * `fixed_team` for leg 1 under `loser_starts` and `winner_starts` too, not
 * only under `fixed` -- see the `STARTER_CONFIG` comment -- so a team picker
 * that remembered "Team B" after the rule moved off `fixed` would quietly hand
 * Team B the first leg. With one field there is nothing to remember: the
 * reducer can only reach the five states below, and each one is a whole
 * `start_rule` and `fixed_team` pair.
 */
export type StarterId = 'alternate' | 'loser_starts' | 'winner_starts' | 'fixed-A' | 'fixed-B'

/**
 * What each starter contributes to the config.
 *
 * `fixed_team` is the team's index in `TEAM_IDS`, which is the order
 * `buildMatch` posts the rosters in, so "Team B" and `teams[1]` cannot come
 * apart. Every rule other than `fixed` sends 0, which is not filler:
 * `starting_team` opens leg 1 at `fixed_team` under `loser_starts` and
 * `winner_starts` (`alternate` alone ignores it and starts at 0). Sending 0
 * means Team A -- whoever was tapped first -- throws first in leg 1 under all
 * three, exactly as every match did before #59. Only "Team B starts" puts B
 * first.
 */
const STARTER_CONFIG: Record<StarterId, Pick<GameConfig, 'start_rule' | 'fixed_team'>> = {
  alternate: { start_rule: 'alternate', fixed_team: 0 },
  loser_starts: { start_rule: 'loser_starts', fixed_team: 0 },
  winner_starts: { start_rule: 'winner_starts', fixed_team: 0 },
  'fixed-A': { start_rule: 'fixed', fixed_team: TEAM_IDS.indexOf('A') },
  'fixed-B': { start_rule: 'fixed', fixed_team: TEAM_IDS.indexOf('B') },
}

export interface StarterOption {
  value: StarterId
  label: string
}

/**
 * The starter chips, in the order a player reads them: the default, the two
 * that follow the last leg, then the two that never move. The labels say who
 * starts, so they read on their own without a heading over them.
 *
 * At one leg to win the first three behave identically, since all of them
 * open leg 1 with Team A. The chips stay anyway (Jack, #59): "Team B starts"
 * still changes who throws first, and hiding the row would hide that too.
 */
export const STARTER_OPTIONS: readonly StarterOption[] = [
  { value: 'alternate', label: 'Alternate' },
  { value: 'loser_starts', label: 'Loser starts' },
  { value: 'winner_starts', label: 'Winner starts' },
  { value: 'fixed-A', label: 'Team A starts' },
  { value: 'fixed-B', label: 'Team B starts' },
]

export interface Assignment {
  playerId: number
  team: TeamId
  /**
   * Where the fill first put them, which is what makes the tap cycle total.
   *
   * Tapping walks `origin` -> the other team -> off the list, so every player
   * can reach both teams whichever one they landed on. Without this the cycle
   * would have to be a fixed A -> B -> off, and anyone the fill dropped on B
   * could never be moved to A.
   */
  origin: TeamId
}

export interface SetupState {
  game: GameId
  /** x01 only. Kept across a switch to cricket so switching back is free. */
  inRule: Rule
  outRule: Rule
  legsToWin: number
  /** Who opens each leg. Applies to every game, so unlike the x01 rules it is
   *  never hidden and never dormant. */
  starter: StarterId
  /** In tap order. At most one entry per player, which is what makes the
   *  server's "a player cannot appear on two teams" true by construction. */
  assignments: readonly Assignment[]
}

/**
 * Legs to win, not best-of.
 *
 * `GameConfig` requires an odd `best_of` -- `CHECK (best_of % 2 = 1)` in
 * `0001_init.sql`, mirrored by `_check_odd_best_of` -- so a stepper over
 * `best_of` would have to step by two and would still read as a stepper whose
 * `+` adds two. Stepping legs by one instead means every value reachable in
 * the UI is legal, and "first to three" is how the count is said out loud.
 * `MAX_LEGS` of 5 is #4's mockup's `max={9}` read as the best-of it was.
 */
export const MIN_LEGS = 1
export const MAX_LEGS = 5

export const INITIAL_STATE: SetupState = {
  game: '501',
  inRule: 'straight',
  outRule: 'double',
  // #4's mockup opens this stepper on 3, and first-to-three is the pub default.
  legsToWin: 3,
  // What every match sent before #59, and what the server defaults to.
  starter: 'alternate',
  assignments: [],
}

export type SetupAction =
  | { type: 'game'; game: GameId }
  | { type: 'inRule'; rule: Rule }
  | { type: 'outRule'; rule: Rule }
  | { type: 'legsToWin'; legs: number }
  | { type: 'starter'; starter: StarterId }
  | { type: 'tapPlayer'; playerId: number }

/**
 * Which team the next player joins: the smaller one, A on a tie.
 *
 * From an empty list that is exactly round-robin -- A, B, A, B -- which is
 * what puts a 2v2 within four taps of the roster and gives 2v1 for three
 * players with no further taps. Phrasing it as "the smaller team" rather than
 * "every other tap" is what keeps it sensible after someone is moved or
 * dropped: the next tap refills the gap instead of counting past it.
 */
function fillTeam(assignments: readonly Assignment[]): TeamId {
  const inA = assignments.filter((a) => a.team === 'A').length
  const inB = assignments.filter((a) => a.team === 'B').length
  return inB < inA ? 'B' : 'A'
}

/** One tap on a player: join, swap, or drop out. */
function tapPlayer(assignments: readonly Assignment[], playerId: number): Assignment[] {
  const current = assignments.find((a) => a.playerId === playerId)
  if (current === undefined) {
    const origin = fillTeam(assignments)
    return [...assignments, { playerId, team: origin, origin }]
  }
  if (current.team === current.origin) {
    return assignments.map((a) => (a.playerId === playerId ? { ...a, team: other(a.origin) } : a))
  }
  return assignments.filter((a) => a.playerId !== playerId)
}

export function reduce(state: SetupState, action: SetupAction): SetupState {
  switch (action.type) {
    case 'game':
      // Deliberately leaves `assignments` alone: #23 requires that changing
      // the game type after picking teams keeps the teams.
      return { ...state, game: action.game }
    case 'inRule':
      return { ...state, inRule: action.rule }
    case 'outRule':
      return { ...state, outRule: action.rule }
    case 'legsToWin':
      return { ...state, legsToWin: Math.min(MAX_LEGS, Math.max(MIN_LEGS, action.legs)) }
    case 'starter':
      return { ...state, starter: action.starter }
    case 'tapPlayer':
      return { ...state, assignments: tapPlayer(state.assignments, action.playerId) }
  }
}

/** The team a player is on, or `null` for one who is not playing. */
export function teamOf(state: SetupState, playerId: number): TeamId | null {
  return state.assignments.find((a) => a.playerId === playerId)?.team ?? null
}

/** Who is on each team, in tap order. */
export function membersOf(state: SetupState, team: TeamId): readonly Assignment[] {
  return state.assignments.filter((a) => a.team === team)
}

/**
 * The body to post, or `null` when this state is not a match yet.
 *
 * `null` is #23's "at least 2 teams and every team has at least 1 player",
 * and it is the only thing the start button consults -- so a state the button
 * lets you start is a state this function has already built a body for.
 */
export function buildMatch(state: SetupState): MatchWrite | null {
  const rosters = TEAM_IDS.map((team) => membersOf(state, team).map((a) => a.playerId))
  if (!rosters.every((ids) => ids.length > 0)) return null

  const config: GameConfig = {
    ...GAME_CONFIG[state.game],
    best_of: 2 * state.legsToWin - 1,
    ...STARTER_CONFIG[state.starter],
  }

  return {
    config: isX01(state.game)
      ? { ...config, in_rule: state.inRule, out_rule: state.outRule }
      : config,
    teams: rosters.map((player_ids) => ({ player_ids })),
  }
}
