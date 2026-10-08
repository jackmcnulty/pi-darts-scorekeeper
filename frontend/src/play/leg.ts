/**
 * The parts of a play payload that are true whatever game is being played.
 *
 * Which leg takes the next dart, whether it will accept one, which leg an undo
 * addresses, which visit the strip shows and which one a bust or a checkout is
 * read from, and how a refusal reads. None of it depends on x01's remaining
 * score or cricket's marks, so it lives here rather than in either game's
 * module -- #24 wrote all of this in `x01.ts` because x01 was the only board
 * there was, and #25 needs the same answers unchanged.
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
 * The visit the dart slots show: the one being thrown, and nothing in between.
 *
 * #24 fell back to `previous_visit` here so the third dart would not vanish
 * the instant it was entered. #32's device pass found the cost: the next
 * thrower stood at the oche looking at the last player's darts until their own
 * first one landed. #69 clears the strip as soon as the turn passes, and Jack
 * chose "immediately" over a brief hold -- a hold would be the first timer on
 * the play screen and a piece of state the payload does not have. The third
 * dart is not lost: the turn visibly passing is what says it landed, and an
 * undo puts the visit back, because the server reopens it as `current_visit`.
 *
 * The one exception is a won match. Nobody throws next, so "empty for the next
 * thrower" means nothing there; the winning darts stay, agreeing with the
 * completion sheet's checkout row and the "won the match" line, and a winning
 * dart entered wrongly is still in front of whoever wants to undo it. That is
 * `current_leg`, because `active_leg` is null once the match is decided. A
 * checkout that wins only the leg needs no exception: `legInPlay` has already
 * rolled on to the new leg, which has no visits yet.
 *
 * This takes the whole state rather than a leg because it needs the match's
 * status; it chooses which payload field to draw, and decides no rule.
 */
export function stripVisit(state: MatchState): Visit | null {
  const leg = legInPlay(state)
  return leg.current_visit ?? (state.is_complete ? leg.previous_visit : null)
}

/**
 * The most recent visit in the leg, finished or not -- #24's original rule.
 *
 * No longer what the dart slots show (`stripVisit` is), but still the right
 * question for three readers. x01's bust line and visit total: both stay up
 * from the visit's last dart until the next dart lands, exactly as before #69.
 * Jack kept the total on this rule so #24's "shows 180 for the visit" is still
 * on screen after the third dart; reading `previous_visit` alone instead would
 * keep both up through the next player's whole visit. And `finishingVisit` on
 * the leg sheet, where the winning visit is the one that just finished.
 */
export function latestVisit(leg: LegState): Visit | null {
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
