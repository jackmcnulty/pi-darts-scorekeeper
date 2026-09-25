/**
 * `MatchStateResponse` payloads to render against, built from the real type.
 *
 * #24 asks for "checkout hint rendering against fixture states, including a
 * non-checkable score", and the states that matter most on this screen are the
 * awkward ones: a bust, a visit that has just ended, a leg won on the last
 * response, a team that has not thrown. Driving a real engine to reach each of
 * those would make the test a test of the engine; stating the payload outright
 * is what makes it a test of the rendering.
 *
 * Everything returned is typed as the generated `MatchState`, so a payload that
 * has drifted from the served schema stops typechecking rather than quietly
 * describing a response the Pi no longer sends. That is the only guard a
 * hand-written fixture can have, and it is why nothing here is `as` cast.
 *
 * Not a `*.test.ts`, so like `test-harness.tsx` it is named explicitly in
 * `tsconfig.test.json`, excluded from `tsconfig.app.json`, and excluded from
 * coverage: it is scaffolding, not app code.
 */
import type { components } from '../api/schema'
import type { Checkout, LegState, MatchState, Visit } from '../api/play'

type DartResponse = components['schemas']['DartResponse']
type TeamResponse = components['schemas']['TeamResponse']
type TeamLeg = components['schemas']['TeamLegResponse']

export const JACK = 1
export const DAD = 2

/** Two solo teams: team 1 is Jack, team 2 is Dad. #23's commonest match. */
export function soloTeams(): TeamResponse[] {
  return [team(1, 0, [[JACK, 'Jack']]), team(2, 1, [[DAD, 'Dad']])]
}

/** A 2v2: Jack with Ellie against Dad with Sam. */
export function pairTeams(): TeamResponse[] {
  return [
    team(1, 0, [
      [JACK, 'Jack'],
      [3, 'Ellie'],
    ]),
    team(2, 1, [
      [DAD, 'Dad'],
      [4, 'Sam'],
    ]),
  ]
}

function team(id: number, team_index: number, members: [number, string][]): TeamResponse {
  return {
    id,
    team_index,
    name: null,
    is_solo: members.length === 1,
    members: members.map(([player_id, display_name], member_index) => ({
      player_id,
      member_index,
      display_name,
      is_archived: false,
    })),
  }
}

/**
 * One dart, labelled the way `Throw.label` labels it.
 *
 * The label is supplied rather than derived: the server is what prints "T20"
 * and "BULL", and a fixture that recomputed the label would be asserting
 * against its own idea of one.
 */
export function dart(dart_index: number, label: string, causedBust = false): DartResponse {
  return {
    dart_id: 100 + dart_index,
    dart_index,
    // Enough of the board to be a real dart; the screen renders `label`.
    segment: 20,
    multiplier: 3,
    label,
    counted: !causedBust,
    caused_bust: causedBust,
    was_checkout_attempt: false,
  }
}

export interface VisitOptions {
  teamId?: number
  playerId?: number
  visitIndex?: number
  scoreBefore: number
  scoreAfter: number
  labels: string[]
  isBust?: boolean
  isComplete?: boolean
  /** Which dart busted it, by index. Only read when `isBust`. */
  bustAt?: number
}

export function visit(options: VisitOptions): Visit {
  const { isBust = false, bustAt = options.labels.length - 1 } = options
  return {
    visit_id: 900 + (options.visitIndex ?? 0),
    team_id: options.teamId ?? 1,
    player_id: options.playerId ?? JACK,
    visit_index: options.visitIndex ?? 0,
    score_before: options.scoreBefore,
    score_after: options.scoreAfter,
    is_bust: isBust,
    is_complete: options.isComplete ?? true,
    darts: options.labels.map((label, index) => dart(index, label, isBust && index === bustAt)),
  }
}

export interface LegOptions {
  legId?: number
  legIndex?: number
  /** x01 remaining, positional to the teams. */
  remaining?: [number, number]
  /**
   * Cricket marks, positional to the teams, as `target -> marks`.
   *
   * Passing this makes the leg a cricket leg: `remaining` and `is_open` go null,
   * the way the server sends them for cricket, so a fixture cannot describe a
   * leg that is somehow both games at once. Targets left out are targets nobody
   * has hit, which the server also omits rather than sending as zero.
   */
  marks?: [Record<number, number>, Record<number, number>]
  /** Cricket points, positional to the teams. */
  points?: [number, number]
  /** Three-dart averages, positional to the teams. Null is "has not thrown". */
  averages?: [number | null, number | null]
  /** Darts thrown, positional to the teams. */
  teamDarts?: [number, number]
  /** Which team throws next, by index, or null for nobody. */
  thrower?: 0 | 1 | null
  /** Which member of that team, by index. */
  throwerMember?: number
  currentVisit?: Visit | null
  previousVisit?: Visit | null
  checkoutPaths?: string[][]
  checkoutReason?: Checkout['reason']
  winnerTeamId?: number | null
  /** Darts in the leg as a whole. What `legToUndo` and `canUndo` read. */
  dartsThrown?: number
  teams?: TeamResponse[]
}

export function leg(options: LegOptions = {}): LegState {
  const {
    legId = 7,
    legIndex = 0,
    remaining = [501, 501],
    marks,
    points = [0, 0],
    averages = [null, null],
    teamDarts = [0, 0],
    thrower = 0,
    throwerMember = 0,
    currentVisit = null,
    previousVisit = null,
    checkoutPaths = [],
    checkoutReason = 'not_checkable',
    winnerTeamId = null,
    dartsThrown = 0,
    teams = soloTeams(),
  } = options

  const throwingTeam = thrower === null ? null : teams[thrower]
  const throwingMember = throwingTeam?.members[throwerMember]

  // A cricket leg exactly when marks were asked for. The server fills one pair
  // of fields or the other and nulls the rest; mirroring that here is what stops
  // a fixture describing a response the Pi would never send.
  const isCricket = marks !== undefined

  const legTeams: TeamLeg[] = teams.map((entry, index) => ({
    team_id: entry.id,
    remaining: isCricket ? null : (remaining[index] ?? 501),
    is_open: isCricket ? null : true,
    darts_thrown: teamDarts[index] ?? 0,
    points: points[index] ?? 0,
    // `dict[int, int]` serialises with string keys -- the wire really carries
    // `{"marks":{"20":3}}` -- so the fixture builds string keys too rather than
    // relying on a numeric index happening to work at runtime.
    marks:
      marks === undefined
        ? null
        : Object.fromEntries(
            Object.entries(marks[index] ?? {}).map(([target, held]) => [String(target), held]),
          ),
    // Null throughout cricket, which is scored by marks per round rather than
    // by an average. #24 added the field and returns null for exactly this.
    three_dart_average: isCricket ? null : (averages[index] ?? null),
  }))

  return {
    leg_id: legId,
    leg_index: legIndex,
    starting_team_id: teams[0]?.id ?? 1,
    winner_team_id: winnerTeamId,
    is_complete: winnerTeamId !== null,
    darts_thrown: dartsThrown,
    darts_left: currentVisit === null ? 3 : 3 - currentVisit.darts.length,
    next_thrower:
      throwingTeam === undefined || throwingTeam === null || throwingMember === undefined
        ? null
        : {
            team_id: throwingTeam.id,
            player_id: throwingMember.player_id,
            display_name: throwingMember.display_name,
          },
    teams: legTeams,
    current_visit: currentVisit,
    previous_visit: previousVisit,
    checkout: {
      team_id: throwingTeam?.id ?? null,
      player_id: throwingMember?.player_id ?? null,
      remaining: isCricket || thrower === null ? null : (remaining[thrower] ?? null),
      darts_left: currentVisit === null ? 3 : 3 - currentVisit.darts.length,
      // `hints.for_leg` returns no paths and `not_x01` for every cricket leg --
      // there is no checkout to draw on that board.
      paths: isCricket ? [] : checkoutPaths,
      // The server's invariant: `reason` is set exactly when `paths` is empty,
      // so the fixture cannot describe a response that breaks it.
      reason: isCricket ? 'not_x01' : checkoutPaths.length > 0 ? null : checkoutReason,
    },
  }
}

export interface MatchStateOptions extends LegOptions {
  matchId?: number
  startScore?: number
  bestOf?: number
  gameType?: 'x01' | 'cricket'
  /** Which cricket. Ignored for an x01 match, as the server ignores it. */
  variant?: 'standard' | 'cutthroat' | 'quick'
  status?: MatchState['status']
  legsWon?: [number, number]
  winner?: number | null
  /** The leg a dart goes to. Null is a won or abandoned match. */
  activeLegId?: number | null
  /**
   * The leg opened behind a leg that was just won. Non-null for exactly one
   * response in a match; see the `darts.api.play` module docstring.
   */
  activeLeg?: LegState | null
}

export function matchState(options: MatchStateOptions = {}): MatchState {
  const {
    matchId = 42,
    startScore = 501,
    bestOf = 3,
    gameType = 'x01',
    variant = 'standard',
    status = 'in_progress',
    legsWon = [0, 0],
    winner = null,
    teams = soloTeams(),
    activeLeg = null,
    ...legOptions
  } = options

  // A cricket match's legs are cricket legs whether or not the caller bothered
  // to say what is on the board, so a test that only cares about the variant
  // still gets marks-shaped teams rather than an x01 leg wearing a cricket
  // config. An empty map is a board nobody has hit yet, which is a real state.
  const current = leg({
    ...legOptions,
    marks: legOptions.marks ?? (gameType === 'cricket' ? [{}, {}] : undefined),
    teams,
  })
  const activeLegId =
    options.activeLegId !== undefined
      ? options.activeLegId
      : status === 'in_progress'
        ? (activeLeg?.leg_id ?? current.leg_id)
        : null

  return {
    match_id: matchId,
    config:
      gameType === 'x01'
        ? {
            game_type: 'x01',
            start_score: startScore,
            in_rule: 'straight',
            out_rule: 'double',
            best_of: bestOf,
            start_rule: 'alternate',
            fixed_team: 0,
          }
        : {
            game_type: 'cricket',
            variant,
            best_of: bestOf,
            start_rule: 'alternate',
            fixed_team: 0,
          },
    status,
    teams,
    legs_won: legsWon,
    winner_team_id: winner,
    is_complete: winner !== null,
    current_leg: current,
    active_leg_id: activeLegId,
    active_leg: activeLeg,
  }
}
