/**
 * Which completion sheet a payload calls for, and what goes on it.
 *
 * #26's first two criteria are one branch: a leg win pauses to say who won it,
 * unless it was the *deciding* leg, in which case the match sheet replaces the
 * leg sheet rather than queueing behind it. Both answers are already on the
 * payload and the branch is mutually exclusive, which is worth spelling out
 * because it is the reason this is not arithmetic about `best_of`:
 *
 * * A non-deciding leg win is the one response in a match with a non-null
 *   `active_leg` -- `services.play` opened the next leg on the winning dart, so
 *   `current_leg` is the leg just won and `active_leg` is where the next dart
 *   goes. See the `darts.api.play` module docstring, "Two legs, not one".
 * * A deciding leg win opens no next leg, so `active_leg` is null and
 *   `is_complete` is true instead.
 *
 * So nothing here counts legs towards `best_of` or compares a tally to a
 * threshold. The server decided the match was over; this reads that decision.
 * A client that re-derived it would be a second implementation of the one rule
 * -- and would get `best_of` wrong for a match that ended early, which every
 * best-of does.
 *
 * Why the leg sheet is transient on purpose
 * ---------------------------------------
 * `active_leg` is non-null for exactly one response, so the leg sheet appears
 * on the winning dart and cannot come back after a reload -- the visit that
 * finished the leg is on no later response. That is deliberate and was ruled on
 * for #26: the sheet is a moment, not a destination. What survives is the
 * result itself, in the tally and in `/stats`, and the dart-by-dart record on
 * the match detail screen. The match sheet *does* survive, because
 * `is_complete` is a durable fact about the match rather than about one
 * response.
 *
 * `legInPlay` is left alone, which is the other half of the same decision. The
 * board underneath a leg sheet is already the new leg, so "continue" is a
 * dismissal and not a state change -- there is no "start next leg" call to make
 * and none is needed. `services.play` opened the leg and applied `start_rule`
 * itself, so the correct starting team is a thing to *display*, from
 * `active_leg.next_thrower`, not a thing to work out.
 */
import type { LegLine, MatchStats } from '../api/history'
import type { LegState, MatchState, Visit } from '../api/play'
import { shownVisit } from './leg'

/** A leg was won and the match goes on. */
export interface LegSheet {
  kind: 'leg'
  /** The leg that was just won. */
  leg: LegState
  /** Where the next dart goes -- already opened by the server. */
  next: LegState
}

/** The match is over. */
export interface MatchSheet {
  kind: 'match'
  /** The leg that decided it. */
  leg: LegState
}

export type SheetDue = LegSheet | MatchSheet

/**
 * The sheet this payload calls for, or null.
 *
 * Order matters and is criterion 1: a complete match is a match sheet even
 * though a leg was also just won, because the deciding leg is both and the
 * match is the thing worth saying.
 */
export function sheetDue(state: MatchState): SheetDue | null {
  if (state.status === 'abandoned') return null
  if (state.is_complete) return { kind: 'match', leg: state.current_leg }
  // The one response in a match where these are two different legs. Anything
  // else -- mid-leg, mid-visit, first load -- has nothing to announce.
  if (state.active_leg !== null && state.active_leg.leg_id !== state.current_leg.leg_id) {
    return { kind: 'leg', leg: state.current_leg, next: state.active_leg }
  }
  return null
}

/**
 * An identity for a sheet, so a dismissal sticks to the one that was dismissed.
 *
 * Keyed on the leg rather than on the kind: dismissing leg 2's sheet must not
 * suppress leg 3's, and the match sheet is its own key because it is about the
 * match. A counter would do neither.
 */
export function sheetKey(sheet: SheetDue): string {
  return sheet.kind === 'match'
    ? `match-${String(sheet.leg.leg_id)}`
    : `leg-${String(sheet.leg.leg_id)}`
}

/** The winning team's name, or a neutral fallback if the payload has no team for it. */
export function winnerName(state: MatchState, teamId: number | null): string {
  const team = state.teams.find((entry) => entry.id === teamId)
  if (team === undefined) return 'Somebody'
  return team.name ?? team.members.map((member) => member.display_name).join(' & ')
}

/**
 * The visit that finished the leg, if this response still carries it.
 *
 * Only ever the winning response has it, which is why the leg sheet does not
 * survive a reload; see the module docstring. Null is a real answer and the
 * sheet omits the row rather than inventing one.
 */
export function finishingVisit(leg: LegState): Visit | null {
  const visit = shownVisit(leg)
  if (visit === null) return null
  // The winning visit belongs to the winning team. A leg whose last visit was
  // somebody else's -- which a reload can produce -- has no checkout to show.
  return visit.team_id === leg.winner_team_id ? visit : null
}

/** "T20 T19 D12", the darts that finished the leg. Empty when there are none. */
export function checkoutDarts(visit: Visit | null): string[] {
  return visit === null ? [] : visit.darts.map((dart) => dart.label)
}

export interface PlayerLine {
  playerId: number
  name: string
  /** The server's number. Null before a player's first dart in scope. */
  average: number | null
  dartsThrown: number
  /** Cricket's metric. Null for x01, as the server sends it. */
  marksPerRound: number | null
}

/**
 * Per-player lines for one leg, from `/stats`.
 *
 * `LegLineResponse` is already per player per leg, so this filters rather than
 * aggregates -- there is no averaging of averages here, which would be a
 * different and wrong number.
 */
export function legLines(stats: MatchStats | undefined, legId: number): PlayerLine[] {
  if (stats === undefined) return []
  const names = new Map(
    stats.match.players.map((player) => [player.player_id, player.display_name]),
  )
  return stats.match.legs
    .filter((line: LegLine) => line.leg_id === legId)
    .map((line) => ({
      playerId: line.player_id,
      name: names.get(line.player_id) ?? `Player ${String(line.player_id)}`,
      average: line.three_dart_average,
      dartsThrown: line.darts_thrown,
      marksPerRound: line.marks_per_round,
    }))
}

/**
 * Per-player lines for the whole match, from the report's own `players` block.
 *
 * Read off `x01.three_dart_average` and `cricket.marks_per_round` rather than
 * combined from the leg lines, because #19 computes a match average over the
 * match's darts and that is not the mean of its legs' averages.
 */
export function matchLines(stats: MatchStats | undefined): PlayerLine[] {
  if (stats === undefined) return []
  return stats.match.players.map((player) => ({
    playerId: player.player_id,
    name: player.display_name,
    average: player.x01.three_dart_average,
    dartsThrown: player.darts_thrown,
    marksPerRound: player.cricket.marks_per_round,
  }))
}

export interface TallyLine {
  teamId: number
  name: string
  legsWon: number
  isWinner: boolean
}

/** The final leg tally, positional to `teams` as `legs_won` is. */
export function tally(state: MatchState): TallyLine[] {
  return state.teams.map((team, index) => ({
    teamId: team.id,
    name: winnerName(state, team.id),
    legsWon: state.legs_won[index] ?? 0,
    isWinner: team.id === state.winner_team_id,
  }))
}

/**
 * How a player line reads aloud.
 *
 * Adjacent spans concatenate with no separator -- "Jack57.2" -- so every row
 * that stacks a name against numbers spells itself out. `x01.ts:cardLabel` is
 * the precedent.
 */
export function playerLineLabel(line: PlayerLine): string {
  const parts = [line.name]
  if (line.average !== null) parts.push(`${line.average.toFixed(1)} three-dart average`)
  if (line.marksPerRound !== null) parts.push(`${line.marksPerRound.toFixed(2)} marks per round`)
  parts.push(`${String(line.dartsThrown)} darts thrown`)
  return parts.join(', ')
}
