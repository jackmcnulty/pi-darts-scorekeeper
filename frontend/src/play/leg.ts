/**
 * The parts of a play payload that are true whatever game is being played.
 *
 * Which leg takes the next dart, whether it will accept one, which leg an undo
 * addresses, which visit to show, and how a refusal reads. None of it depends
 * on x01's remaining score or cricket's marks, so it lives here rather than in
 * either game's module -- #24 wrote all of this in `x01.ts` because x01 was the
 * only board there was, and #25 needs the same seven answers unchanged.
 *
 * Nothing here computes a rule. The thrower, the tally, the bust and the
 * refusal reason all arrive on `MatchStateResponse` already decided; these
 * functions choose which of them to read. If something in this file ever looks
 * like arithmetic about darts, the payload probably already has the answer.
 */
import type { LegState, MatchState, Visit } from '../api/play'

/** The three dart slots in a visit, filled or not. Both games throw three. */
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
 * one starts -- which in x01 is also what puts a bust in front of the player
 * who caused it for longer than one repaint.
 */
export function shownVisit(leg: LegState): Visit | null {
  return leg.current_visit ?? leg.previous_visit
}

/**
 * The 409s the play screen can provoke, said in words a player can act on.
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
