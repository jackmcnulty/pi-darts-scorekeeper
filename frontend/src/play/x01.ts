/**
 * Everything the x01 screen shows, derived from one payload in one place.
 *
 * The same shape as `setup/config.ts`: the pure decisions live in a `.ts` and
 * `Play.tsx` only renders, which is what lets `x01.test.ts` enumerate the
 * awkward states -- a bust, a won leg, a team yet to throw -- against fixtures
 * instead of driving them through the UI.
 *
 * Nothing here computes a rule. The checkout paths, the bust and its reverted
 * score, the thrower and the tally all arrive on `MatchStateResponse` already
 * decided; these functions choose which of them to show and what to call it.
 * If a function in this file ever looks like arithmetic about darts, the
 * payload probably already has the answer.
 */
import type { components } from '../api/schema'
import type { Checkout, LegState, MatchState, Visit } from '../api/play'

type NoHintsReason = components['schemas']['NoHintsReason']

/** The three dart slots, filled or not. */
export const VISIT_SIZE = 3

/**
 * Where the next dart goes.
 *
 * `active_leg` is non-null for exactly one response in a match -- the one
 * reporting a leg being won, where `current_leg` is the leg that was just
 * finished. Preferring it means the board rolls straight on to the new leg and
 * the win shows up in the tally. #26 owns the interstitial that pauses to say
 * who won it; until then rolling on is better than showing a finished leg
 * nobody can throw into.
 */
export function legInPlay(state: MatchState): LegState {
  return state.active_leg ?? state.current_leg
}

/** Whether this leg will accept a dart: not won, not abandoned, has a thrower. */
export function isPlayable(state: MatchState): boolean {
  return (
    state.status === 'in_progress' &&
    state.active_leg_id !== null &&
    legInPlay(state).next_thrower !== null
  )
}

/**
 * The leg an undo should address: whichever one still has a last dart.
 *
 * Normally that is the leg in play. At a leg boundary it is not: `legInPlay`
 * has rolled on to a leg with no darts in it, and the dart somebody wants back
 * is the one that won the previous leg. `play.undo` supports exactly this --
 * it reopens the won leg, closes the empty leg opened behind it, and unwins the
 * match if that leg decided it -- and refuses only when a *later* leg has been
 * thrown into. So naming the leg with the darts is both what the player means
 * and what the server accepts.
 */
export function legToUndo(state: MatchState): LegState {
  const inPlay = legInPlay(state)
  return inPlay.darts_thrown > 0 ? inPlay : state.current_leg
}

/**
 * Whether there is a dart to take back.
 *
 * Deliberately not tied to `isPlayable`: a won match accepts no darts but can
 * still be undone, which is the only way to fix a mis-entered winning dart. An
 * abandoned one refuses both.
 */
export function canUndo(state: MatchState): boolean {
  return state.status !== 'abandoned' && legToUndo(state).darts_thrown > 0
}

/**
 * The visit to show in the dart slots.
 *
 * `current_visit` is null for the third of the time between a visit ending and
 * the next dart landing, so showing only that would make the third dart of
 * every visit vanish the instant it was entered. Falling back to
 * `previous_visit` keeps the visit that just finished on screen until the next
 * one starts -- which is also what puts a bust in front of the player who
 * caused it for longer than one repaint.
 */
export function shownVisit(leg: LegState): Visit | null {
  return leg.current_visit ?? leg.previous_visit
}

/** What the shown visit has scored. 0 for a bust, which is the point of a bust. */
export function visitTotal(visit: Visit): number {
  return visit.score_before - visit.score_after
}

export interface Bust {
  /** The dart that did it, as the server labelled it: "T20", "BULL". */
  dart: string
  /** The score the visit went back to. */
  revertedTo: number
}

/**
 * The bust to announce, or null.
 *
 * Read off the payload rather than worked out: `is_bust` is the server's
 * verdict, `score_after` is already the pre-visit score because the revert
 * happened there, and `caused_bust` names the dart. A client that recomputed
 * any of the three would be a second implementation of the bust rules.
 */
export function bustOf(visit: Visit | null): Bust | null {
  if (visit === null || !visit.is_bust) return null
  const dart = visit.darts.find((d) => d.caused_bust)
  return { dart: dart?.label ?? '', revertedTo: visit.score_after }
}

/** Why there is no checkout to show. Closed set, so the mapping is exhaustive. */
const NO_HINT_TEXT: Record<NoHintsReason, string> = {
  not_checkable: 'No finish',
  not_open: 'Not open yet',
  leg_complete: 'Leg won',
  match_abandoned: 'Abandoned',
  no_thrower: '—',
  not_x01: '—',
}

/**
 * The checkout strip: the best finish, or why there is not one.
 *
 * `paths` is empty exactly when `reason` is set, so this never has to decide
 * which of the two to believe. The paths are already ordered best-first and
 * already account for the out-rule and the darts left in the visit, so the
 * "updates after every dart and respects both" criterion is discharged by
 * rendering whatever the latest response carried.
 */
export function checkoutText(checkout: Checkout): string {
  const best = checkout.paths[0]
  if (best !== undefined) return best.join(' ')
  return checkout.reason === null ? '—' : NO_HINT_TEXT[checkout.reason]
}

/**
 * The 409s this screen can provoke, said in words a player can act on.
 *
 * Keyed on `detail.reason`, never on the message: several distinct refusals
 * share the 409 `conflict` code and the server tells them apart with the
 * discriminator for exactly this purpose. `PlayerForm.tsx` is the precedent.
 */
const REFUSAL_TEXT: Record<string, string> = {
  leg_complete: 'That leg has been won already.',
  match_complete: 'The match is over.',
  nothing_to_undo: 'There is nothing left to undo.',
  match_abandoned: 'This match was abandoned.',
  idempotency_conflict: 'That dart was already recorded as a different throw.',
}

/**
 * What to show when a dart or an undo was refused.
 *
 * Falls through to the server's own message, which #16's envelope always
 * carries, so an unrecognised failure still says something true.
 */
export function refusalText(error: Error, reason: string | null): string {
  return (reason === null ? undefined : REFUSAL_TEXT[reason]) ?? error.message
}

export interface TeamCard {
  teamId: number
  /** Whoever is at the oche for this team, or its first member. */
  name: string
  /** The rest of the team, for a 2v2. Undefined for a solo team. */
  teammates?: string
  /** x01 remaining. Null only in cricket, which this screen does not render. */
  score: number | null
  legsWon: number
  /** Null before the team's first dart; the server's number, not ours. */
  average: number | null
  active: boolean
  accent: string
}

/**
 * One card per team, with `legs_won` zipped on.
 *
 * `legs_won` is positional to `teams` and `noUncheckedIndexedAccess` is set, so
 * it is zipped exactly once here rather than indexed at each use.
 *
 * Which name leads is #24's call for a 2v2, where `ScoreCard` has one name slot
 * and two unbounded display names will not fit beside a 60px score. The card
 * leads with whoever is actually throwing, because that is the thing the screen
 * exists to say; a team whose turn it is not leads with its first member, and
 * either way the rest of the team is on the second line. `MemberResponse` has
 * no `short_name` to use instead -- #22 flagged that widening and it is still
 * open.
 */
export function teamCards(state: MatchState, leg: LegState): TeamCard[] {
  const thrower = leg.next_thrower
  const byId = new Map(leg.teams.map((team) => [team.team_id, team]))

  return state.teams.map((team, index) => {
    const active = thrower?.team_id === team.id
    const names = team.members.map((member) => member.display_name)
    const lead = active ? (thrower?.display_name ?? names[0]) : names[0]
    const rest = names.filter((name) => name !== lead)
    const inLeg = byId.get(team.id)

    return {
      teamId: team.id,
      name: lead ?? `Team ${String(index + 1)}`,
      teammates: rest.length > 0 ? rest.join(', ') : undefined,
      score: inLeg?.remaining ?? null,
      // `?? 0` is unreachable -- the server builds `legs_won` from `teams` --
      // but the index signature is optional and a team has won no legs until
      // it has won one.
      legsWon: state.legs_won[index] ?? 0,
      average: inLeg?.three_dart_average ?? null,
      active,
      accent: `var(--accent-${String((index % 8) + 1)})`,
    }
  })
}

/**
 * How a card reads aloud.
 *
 * `ScoreCard` stacks name, score and meta as adjacent spans, which a screen
 * reader runs together -- "Jack134Legs 1". Spelled out for the same reason
 * `Setup.tsx` spells out its player rows.
 */
export function cardLabel(card: TeamCard): string {
  const parts = [card.name]
  if (card.teammates !== undefined) parts.push(`with ${card.teammates}`)
  if (card.score !== null) parts.push(`${String(card.score)} remaining`)
  parts.push(`${String(card.legsWon)} ${card.legsWon === 1 ? 'leg' : 'legs'} won`)
  if (card.active) parts.push('throwing now')
  return parts.join(', ')
}

/**
 * The line along the top: which game, which leg, how long the match is.
 *
 * `leg_index` is 0-based on the wire and 1-based to a human. `best_of` is the
 * match's denominator; #23 only ever sets an odd one, as the schema requires.
 */
export function contextLine(state: MatchState, leg: LegState): string {
  const game = state.config.start_score ?? state.config.game_type
  const legNumber = leg.leg_index + 1
  return `${String(game)} · Leg ${String(legNumber)} · Best of ${String(state.config.best_of)}`
}

/**
 * A fresh id for one dart.
 *
 * Opaque text the server stores in a unique column; it needs to be unlikely to
 * collide and nothing more. `randomUUID` is the obvious source and is available
 * in every browser that can run this app over HTTPS or on localhost, but the Pi
 * serves the app over plain HTTP on the LAN, where `crypto` is not a secure
 * context and `randomUUID` may be absent -- hence the fallback, which is why
 * this is a function here rather than a call inline.
 */
export function mintDartId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `dart-${String(Date.now())}-${Math.random().toString(36).slice(2, 10)}`
}
