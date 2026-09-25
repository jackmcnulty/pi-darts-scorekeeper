/**
 * The x01 play screen: the one that has to work with three darts in one hand.
 *
 * It renders and it posts, and that is deliberately all it does. Every write
 * returns the whole new `MatchStateResponse`, so a dart is one round trip and
 * one repaint -- there is no optimistic update, no client-side bust revert, no
 * recomputed checkout and no patched-up score. The checkout paths, the bust and
 * the score it reverted to, the thrower, both visits and the leg tally are all
 * on the payload already. Anything in here that looked like game logic would be
 * a second implementation of rules the server owns.
 *
 * The decisions are in `play/x01.ts` and `play/keypad.ts`, the same split
 * `setup/config.ts` uses: pure functions in a `.ts` that tests can enumerate
 * against, and a component that only renders them.
 *
 * One route, two games
 * --------------------
 * `/play/:matchId` serves both game types, because a match knows which it is
 * and the player only ever taps "play". This file owns the x01 board and
 * everything neither board can differ on -- the match id, the loading and error
 * states, the wake lock, the three hooks -- and hands a cricket match to
 * #25's `CricketBoard`. Both draw inside the same `PlayFrame`, which is the
 * fixed-height shell the whole screen budget depends on.
 *
 * Why a second tap is dropped
 * ---------------------------
 * #24 asks that a double tap not submit two darts, and attributes that to
 * #15's `client_dart_id` idempotency. It is not what idempotency does: a fresh
 * id per tap makes two taps two different darts, and the server records both,
 * correctly. So the suppression is here -- while a request is in flight, a tap
 * on a throw key is ignored outright. On the LAN that window is a couple of
 * milliseconds, which is why the keys are not greyed out for it and why
 * T20-T20-T20 entered as fast as somebody can tap still lands three darts.
 *
 * Where idempotency does earn its keep is the retry. Mutations never retry
 * automatically, because a dart whose request timed out may well have been
 * recorded; so a failed dart keeps its id and "Try again" re-sends the byte-identical
 * body, which the server either records once or recognises and returns the
 * current state for.
 */
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { isRetryable, reasonOf } from '../api/client'
import { useMatchState, useRecordDart, useUndoDart, type DartInput } from '../api/play'
import { Button } from '../components/Button'
import { Keypad } from '../components/Keypad'
import { ScoreCard } from '../components/ScoreCard'
import { DEFAULT_MULTIPLIER, dartFor, type KeypadKey, type Multiplier } from '../play/keypad'
import {
  canUndo,
  isPlayable,
  legInPlay,
  legToUndo,
  mintDartId,
  refusalText,
  shownVisit,
  VISIT_SIZE,
} from '../play/leg'
import { useWakeLock } from '../play/wakeLock'
import { bustOf, cardLabel, checkoutText, contextLine, teamCards, visitTotal } from '../play/x01'
import { CricketBoard } from './CricketBoard'
import { PlayFrame } from './PlayFrame'
import './Play.css'

export function Play() {
  const params = useParams()
  // A path parameter is whatever was typed. `/play/nonsense` reaches this
  // component, and asking the server about match NaN would be a 422 reported as
  // if the Pi had a problem.
  const parsed = Number(params.matchId)
  const matchId = Number.isInteger(parsed) && parsed > 0 ? parsed : null

  const state = useMatchState(matchId ?? 0)
  const recordDart = useRecordDart(matchId ?? 0)
  const undoDart = useUndoDart(matchId ?? 0)
  const [latch, setLatch] = useState<Multiplier>(DEFAULT_MULTIPLIER)

  const match = state.data
  // The lock follows the match, which is #24's "awake during an active match
  // and released when it ends". A won or abandoned match releases it, and so
  // does leaving the screen.
  useWakeLock(match !== undefined && match.status === 'in_progress')

  if (matchId === null) {
    return (
      <PlayFrame>
        <p className="play__note">
          That is not a match. <Link to="/">Back to the start</Link>
        </p>
      </PlayFrame>
    )
  }

  if (state.isPending) {
    return (
      <PlayFrame>
        <p className="play__note">Reading the scoreboard&hellip;</p>
      </PlayFrame>
    )
  }

  if (state.isError) {
    return (
      <PlayFrame>
        <div className="play__note" role="alert">
          <p>{state.error.message}</p>
          <Button variant="secondary" onClick={() => void state.refetch()}>
            Try again
          </Button>
        </div>
      </PlayFrame>
    )
  }

  if (match === undefined) return null

  // Two boards, one route. The match knows which game it is and the player only
  // ever taps "play"; everything above this line -- the id check, the loading
  // and error states, the wake lock, the three hooks -- is the same either way,
  // so the split is here rather than at the router.
  if (match.config.game_type === 'cricket') {
    return (
      <CricketBoard
        match={match}
        latch={latch}
        onLatchChange={setLatch}
        recordDart={recordDart}
        undoDart={undoDart}
      />
    )
  }

  const leg = legInPlay(match)
  const cards = teamCards(match, leg)
  const visit = shownVisit(leg)
  const bust = bustOf(visit)
  const playable = isPlayable(match)
  // One flag for both mutations: a dart and an undo are both a write against
  // the same leg, and letting one start while the other is in flight is how a
  // scoreboard ends up painting the older of two answers.
  const busy = recordDart.isPending || undoDart.isPending
  const failure = recordDart.error ?? undoDart.error
  const retryable = failure !== null && isRetryable(failure)

  const onKey = (key: KeypadKey) => {
    if (busy || !playable || match.active_leg_id === null) return
    const body = { ...dartFor(key, latch), client_dart_id: mintDartId() }
    // Reset on the tap that sends, not on the response: the latch must never
    // still be on Triple when the next key is pressed, which is the whole
    // reason #24 chose a resetting latch over a sticky one. A tap dropped by
    // the guard above does not reach here, so a suppressed tap leaves it alone.
    setLatch(DEFAULT_MULTIPLIER)
    recordDart.reset()
    undoDart.reset()
    recordDart.mutate({ legId: match.active_leg_id, body })
  }

  const onUndo = () => {
    if (busy) return
    recordDart.reset()
    undoDart.reset()
    undoDart.mutate(legToUndo(match).leg_id)
  }

  // The same body, the same `client_dart_id`. That is what makes retrying a
  // dart that may already have been recorded safe rather than a guess.
  const retry = () => {
    const pending: DartInput | undefined = recordDart.variables
    if (recordDart.isError && pending !== undefined) {
      recordDart.mutate(pending)
      return
    }
    if (undoDart.isError && undoDart.variables !== undefined) undoDart.mutate(undoDart.variables)
  }

  return (
    <PlayFrame context={contextLine(match, leg)}>
      <div className="play__cards">
        {cards.map((card) => (
          <ScoreCard
            key={card.teamId}
            name={card.name}
            subtitle={card.teammates}
            score={card.score}
            accent={card.accent}
            active={card.active}
            average={card.average}
            legs={card.legsWon}
            label={cardLabel(card)}
          />
        ))}
      </div>

      <div className="play__turn">
        <div className="play__darts" role="group" aria-label="This visit">
          {Array.from({ length: VISIT_SIZE }, (_, index) => {
            const dart = visit?.darts[index]
            const slot = `Dart ${String(index + 1)}`
            return (
              <span
                key={index}
                className={`play__dart tnum${dart === undefined ? ' play__dart--empty' : ''}`}
                aria-label={dart === undefined ? `${slot}, not thrown` : `${slot}, ${dart.label}`}
              >
                {dart?.label ?? '–'}
              </span>
            )
          })}
        </div>

        {visit !== null && visit.darts.length > 0 && (
          <span
            className="play__visit-total tnum"
            aria-label={`Visit scored ${String(visitTotal(visit))}`}
          >
            {visitTotal(visit)}
          </span>
        )}

        <div className="play__checkout">
          <span className="play__checkout-label">Checkout</span>
          <span className="play__checkout-value tnum">{checkoutText(leg.checkout)}</span>
        </div>
      </div>

      {bust !== null && (
        <p className="play__bust" role="status">
          Bust on {bust.dart} — back to {bust.revertedTo}
        </p>
      )}

      {match.is_complete && (
        <p className="play__done" role="status">
          {cards.find((card) => card.teamId === match.winner_team_id)?.name ?? 'Somebody'} won the
          match. <Link to="/">Back to the start</Link>
        </p>
      )}

      {match.status === 'abandoned' && (
        <p className="play__done" role="status">
          This match was abandoned. <Link to="/">Back to the start</Link>
        </p>
      )}

      {failure !== null && (
        <div className="play__error" role="alert">
          <span>{refusalText(failure, reasonOf(failure))}</span>
          {retryable && (
            <Button variant="secondary" onClick={retry}>
              Try again
            </Button>
          )}
        </div>
      )}

      <Keypad
        latch={latch}
        onLatchChange={setLatch}
        onKey={onKey}
        onUndo={onUndo}
        disabled={busy || !playable}
        undoDisabled={busy || !canUndo(match)}
      />
    </PlayFrame>
  )
}
