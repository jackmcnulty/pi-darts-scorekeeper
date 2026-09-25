/**
 * History payloads to render against, built from the real generated types.
 *
 * The same bargain as `play/statefixture.ts`: everything returned is typed as
 * the generated response, so a fixture that has drifted from the served schema
 * stops typechecking rather than quietly describing something the Pi no longer
 * sends. Nothing here is `as` cast, which is the only guard a hand-written
 * fixture can have.
 *
 * `bustedVisit` is the one that matters most. Criterion 3 is about a visit whose
 * darts were thrown and scored nothing, and the server expresses that as three
 * separate facts -- `is_bust` on the visit, `counted: false` on every dart of
 * it, `caused_bust` on the one that did it, and `score_after === score_before`
 * because the revert already happened. A fixture that set only `is_bust` would
 * let a renderer pass the test by reading the one flag it happened to check, so
 * this sets all four the way `services.play` does.
 *
 * Not a `*.test.ts`, so like `statefixture.ts` it is named explicitly in
 * `tsconfig.test.json`, excluded from `tsconfig.app.json`, and excluded from
 * coverage: it is scaffolding, not app code.
 */
import type { LegHistory, MatchHistory, MatchPage, MatchStats } from '../api/history'
import type { Match } from '../api/matches'
import type { components } from '../api/schema'
import type { Visit } from '../api/play'

type DartResponse = components['schemas']['DartResponse']
type TeamResponse = components['schemas']['TeamResponse']
type MatchStatus = components['schemas']['MatchStatus']

export const JACK = 1
export const DAD = 2

/** Two solo teams: team 1 is Jack, team 2 is Dad. The commonest match. */
export function soloTeams(): TeamResponse[] {
  return [team(1, 0, [[JACK, 'Jack']]), team(2, 1, [[DAD, 'Dad']])]
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

export interface MatchOptions {
  id?: number
  status?: MatchStatus
  bestOf?: number
  gameType?: 'x01' | 'cricket'
  variant?: 'standard' | 'cutthroat' | 'quick'
  startScore?: number
  createdAt?: string
  teams?: TeamResponse[]
  winner?: number | null
}

/** One `MatchResponse`, as the list and the detail header both read it. */
export function match(options: MatchOptions = {}): Match {
  const {
    id = 42,
    status = 'complete',
    bestOf = 3,
    gameType = 'x01',
    variant = 'standard',
    startScore = 501,
    createdAt = '2026-03-03T19:30:00Z',
    teams = soloTeams(),
    winner = 1,
  } = options

  return {
    id,
    status,
    created_at: createdAt,
    completed_at: status === 'complete' ? '2026-03-03T20:05:00Z' : null,
    abandoned_at: status === 'abandoned' ? '2026-03-03T20:05:00Z' : null,
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
    teams,
    current_leg_id: 7,
    winner_team_id: status === 'complete' ? winner : null,
  }
}

/** A page of matches, with the `total` the pager reads. */
export function matchPage(items: Match[], total: number, limit = 20, offset = 0): MatchPage {
  return { items, total, limit, offset }
}

function dart(
  dart_index: number,
  label: string,
  counted: boolean,
  causedBust = false,
): DartResponse {
  return {
    dart_id: 100 + dart_index,
    dart_index,
    // Enough of the board to be a real dart; the screen renders `label`.
    segment: 20,
    multiplier: 3,
    label,
    counted,
    caused_bust: causedBust,
    was_checkout_attempt: false,
  }
}

export interface VisitOptions {
  visitId?: number
  visitIndex?: number
  teamId?: number
  playerId?: number
  scoreBefore: number
  scoreAfter: number
  labels: string[]
}

/** A visit that scored what it scored. */
export function visit(options: VisitOptions): Visit {
  return {
    visit_id: options.visitId ?? 900 + (options.visitIndex ?? 0),
    team_id: options.teamId ?? 1,
    player_id: options.playerId ?? JACK,
    visit_index: options.visitIndex ?? 0,
    score_before: options.scoreBefore,
    score_after: options.scoreAfter,
    is_bust: false,
    is_complete: true,
    darts: options.labels.map((label, index) => dart(index, label, true)),
  }
}

export interface BustOptions extends Omit<VisitOptions, 'scoreAfter'> {
  /** Which dart busted it, by index. The last one by default. */
  bustAt?: number
}

/**
 * A busted visit, with all four facts the server records about one.
 *
 * `score_after === score_before` because the revert already happened, every
 * dart is `counted: false` because an x01 bust voids the whole visit, and the
 * offending dart carries `caused_bust`. See the module docstring.
 */
export function bustedVisit(options: BustOptions): Visit {
  const bustAt = options.bustAt ?? options.labels.length - 1
  return {
    visit_id: options.visitId ?? 900 + (options.visitIndex ?? 0),
    team_id: options.teamId ?? 1,
    player_id: options.playerId ?? JACK,
    visit_index: options.visitIndex ?? 0,
    score_before: options.scoreBefore,
    // The bust put the score back where the visit found it.
    score_after: options.scoreBefore,
    is_bust: true,
    is_complete: true,
    darts: options.labels.map((label, index) => dart(index, label, false, index === bustAt)),
  }
}

export interface LegOptions {
  legId?: number
  legIndex?: number
  winnerTeamId?: number | null
  visits?: Visit[]
  startingTeamId?: number
}

export function leg(options: LegOptions = {}): LegHistory {
  const { legId = 7, legIndex = 0, winnerTeamId = 1, visits = [], startingTeamId = 1 } = options
  return {
    leg_id: legId,
    leg_index: legIndex,
    starting_team_id: startingTeamId,
    winner_team_id: winnerTeamId,
    is_complete: winnerTeamId !== null,
    visits,
  }
}

export function matchHistory(legs: LegHistory[], matchId = 42): MatchHistory {
  return { match_id: matchId, legs }
}

export interface LegLineOptions {
  legId: number
  legIndex?: number
  playerId: number
  dartsThrown?: number
  won?: boolean
  average?: number | null
  marksPerRound?: number | null
}

/**
 * A `MatchReportResponse`, which is where both sheets get their per-player
 * numbers. Only the fields #26 reads are varied; the rest are the zeroes the
 * server would send for a player who has done nothing else.
 */
export function matchStats(
  lines: LegLineOptions[],
  players: { playerId: number; name: string; average?: number | null; darts?: number }[],
  matchId = 42,
): MatchStats {
  return {
    filter: { game_type: null, variant: null, since: null, match_id: matchId },
    match: {
      match_id: matchId,
      game_type: 'x01',
      variant: null,
      players: players.map((player) => ({
        player_id: player.playerId,
        display_name: player.name,
        is_archived: false,
        darts_thrown: player.darts ?? 45,
        legs_played: 1,
        legs_won: 1,
        matches_played: 1,
        matches_won: 1,
        segments: [],
        x01: {
          darts_thrown: player.darts ?? 45,
          visits: 15,
          points_scored: 501,
          three_dart_average: player.average ?? 57.2,
          first_nine_average: 60,
          first_nine_darts: 9,
          highest_visit: 180,
          average_visit: 57.2,
          bands: { one_eighties: 1, one_forty_plus: 2, hundred_plus: 4, sixty_plus: 8 },
          checkout_attempts: 3,
          checkouts_hit: 1,
          checkout_percentage: 33.3,
          best_checkout: 40,
        },
        cricket: {
          darts_thrown: 0,
          darts_on_target: 0,
          marks: 0,
          marks_per_round: null,
          targets: [],
          wasted_darts: 0,
        },
      })),
      legs: lines.map((line) => ({
        leg_id: line.legId,
        leg_index: line.legIndex ?? 0,
        player_id: line.playerId,
        darts_thrown: line.dartsThrown ?? 15,
        won: line.won ?? false,
        three_dart_average: line.average ?? 57.2,
        marks_per_round: line.marksPerRound ?? null,
      })),
    },
  }
}
