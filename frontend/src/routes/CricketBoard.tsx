/**
 * The American Cricket board: seven rows, a column per team, and #24's keypad.
 *
 * Draws what `play/cricket.ts` decided and nothing else. The marks and the
 * points are counted by `engine/cricket.py` and arrive on the payload; this
 * renders them, in the order the scoreboard reads.
 *
 * Why this is not #4's mockup
 * ---------------------------
 * `style/mockups/CricketMockup.tsx` drew an eight-key keypad -- the six numbers,
 * a bull and a miss. It fits beautifully and it makes a dart at 12 literally
 * unreachable, which contradicts #25's own scope line ("non-target numbers
 * dimmed but still enterable") and its fifth criterion. It also has one BULL key
 * where cricket needs two, since the outer bull is one mark and the inner is
 * two, and a board with one bull key cannot record the difference.
 *
 * So this takes the shared 23-key keypad instead, and the height comes out of
 * the board. Measured in Chrome at 402x781 -- the 402x874 device less the 59px
 * Dynamic Island and 34px home indicator insets -- the mockup's board needs
 * 410px and the budget beside a full keypad is 323px. Rows at 36px rather than
 * 44, a compact header rather than the mockup's stacked one, and a 2px row gap
 * bring it to 318px with the keys measured at 57.2px, above the 56px touch
 * floor. That is the trade Jack took: correctness over fidelity, because a dart
 * at 12 is still a dart.
 */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { isRetryable, reasonOf } from '../api/client'
import type { MatchState } from '../api/play'
import type { UseMutationResult } from '@tanstack/react-query'
import { Button } from '../components/Button'
import { Keypad } from '../components/Keypad'
import {
  boardView,
  cellKey,
  changesBetween,
  columnLabel,
  contextLine,
  NON_TARGETS,
  NO_CHANGES,
  type BoardChanges,
  type BoardView,
} from '../play/cricket'
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
import type { DartInput } from '../api/play'
import { PlayFrame } from './PlayFrame'
import './CricketBoard.css'

export interface CricketBoardProps {
  match: MatchState
  latch: Multiplier
  onLatchChange: (latch: Multiplier) => void
  recordDart: UseMutationResult<MatchState, Error, DartInput>
  undoDart: UseMutationResult<MatchState, Error, number>
}

export function CricketBoard({
  match,
  latch,
  onLatchChange,
  recordDart,
  undoDart,
}: CricketBoardProps) {
  const leg = legInPlay(match)
  const view = boardView(match, leg)
  const visit = shownVisit(leg)
  const playable = isPlayable(match)

  // What moved since the last payload, for the two signals #25 asks for: a
  // number closing, and cut-throat points landing on somebody other than the
  // thrower.
  //
  // Keyed on the payload's identity rather than on the view's: react-query
  // hands back the same `MatchState` object until a new response replaces it,
  // so `[match]` fires exactly once per payload and never on a re-render that
  // changed nothing. The view is rebuilt inside the effect for the same reason
  // -- it is a fresh object every render, so depending on it would fire every
  // time. The first payload reports nothing, so a board opened mid-match does
  // not announce every already-closed number at once.
  const [changes, setChanges] = useState<BoardChanges>(NO_CHANGES)
  const seen = useRef<BoardView | null>(null)
  useEffect(() => {
    const now = boardView(match, legInPlay(match))
    setChanges(seen.current === null ? NO_CHANGES : changesBetween(seen.current, now))
    seen.current = now
  }, [match])

  // One flag for both mutations: a dart and an undo are both a write against
  // the same leg, and letting one start while the other is in flight is how a
  // scoreboard ends up painting the older of two answers.
  const busy = recordDart.isPending || undoDart.isPending
  const failure = recordDart.error ?? undoDart.error
  const retryable = failure !== null && isRetryable(failure)

  const onKey = (key: KeypadKey) => {
    if (busy || !playable || match.active_leg_id === null) return
    const body = { ...dartFor(key, latch), client_dart_id: mintDartId() }
    // Reset on the tap that sends, not on the response, exactly as x01 does: a
    // latch still on Triple when the next key is pressed is the mis-entry the
    // resetting latch exists to prevent.
    onLatchChange(DEFAULT_MULTIPLIER)
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

  // The same body, the same `client_dart_id`: what makes retrying a dart that
  // may already have been recorded safe rather than a guess.
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
      <div className="cricket">
        {/* Two columns with the target spine down the middle, as #4 drew it.
            #23's setup screen builds exactly two teams -- `TEAM_IDS` is
            ['A', 'B'] -- so that is the only shape this board is ever handed;
            a third team would want the spine moved to the left, which is one
            grid rule in `CricketBoard.css` to revisit if #23 ever grows one. */}
        <div className="cricket__header">
          {view.columns.map((column, index) => (
            <div
              key={column.teamId}
              className={`cricket__head${column.active ? ' cricket__head--active' : ''}`}
              // Placed rather than ordered: with only two items in a
              // three-column grid, `order` sequences them but auto-placement
              // still drops the second one into the middle column -- the spine.
              style={{ gridColumn: index * 2 + 1 }}
              aria-label={columnLabel(column, view.showsPoints)}
            >
              <span className="cricket__head-name">
                <span className="cricket__dot" style={{ background: column.accent }} />
                {column.name}
              </span>
              {/* #25: hidden entirely in quick, not greyed. Every total is zero
                  for the whole leg there, and a column of zeroes invites the
                  player to wonder what moves it. */}
              {view.showsPoints && (
                <span
                  // Remounts when the total changes, which is what replays the
                  // CSS animation without a timer or a piece of state.
                  key={column.points}
                  className={`cricket__head-points tnum${
                    changes.gained.has(column.teamId) ? ' cricket__head-points--gained' : ''
                  }`}
                >
                  {column.points}
                </span>
              )}
            </div>
          ))}
        </div>

        <div className="cricket__board" role="table" aria-label="Cricket board">
          {view.rows.map((row) => (
            <div key={row.target} className="cricket__row" role="row">
              {/* The row header leads in the DOM, which is the order a screen
                  reader should hear it; the explicit column is what puts the
                  spine between the two columns on screen. */}
              <div className="cricket__target tnum" role="rowheader" style={{ gridColumn: 2 }}>
                {row.label}
              </div>
              {row.cells.map((cell, index) => (
                <div
                  key={cell.teamId}
                  className="cricket__marks"
                  role="cell"
                  style={{ gridColumn: index * 2 + 1 }}
                  data-state={cell.state}
                  data-marks={cell.marks}
                  data-closing={changes.closed.has(cellKey(cell.teamId, row.target))}
                  aria-label={cell.label}
                >
                  {cell.glyph}
                </div>
              ))}
            </div>
          ))}
        </div>

        {/* The dart history for the visit on screen. #25's fifth criterion asks
            that a dart at a non-target still appear, and a 12 moves no mark at
            all -- so without this row, entering one would give the player no
            feedback whatsoever that it had been recorded. */}
        <div className="cricket__visit" role="group" aria-label="This visit">
          {Array.from({ length: VISIT_SIZE }, (_, index) => {
            const dart = visit?.darts[index]
            const slot = `Dart ${String(index + 1)}`
            return (
              <span
                key={index}
                className={`cricket__dart tnum${dart === undefined ? ' cricket__dart--empty' : ''}`}
                aria-label={dart === undefined ? `${slot}, not thrown` : `${slot}, ${dart.label}`}
              >
                {dart?.label ?? '–'}
              </span>
            )
          })}
        </div>

        {match.is_complete && (
          <p className="cricket__done" role="status">
            {view.columns.find((column) => column.teamId === match.winner_team_id)?.name ??
              'Somebody'}{' '}
            won the match. <Link to="/">Back to the start</Link>
          </p>
        )}

        {match.status === 'abandoned' && (
          <p className="cricket__done" role="status">
            This match was abandoned. <Link to="/">Back to the start</Link>
          </p>
        )}

        {failure !== null && (
          <div className="cricket__error" role="alert">
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
          onLatchChange={onLatchChange}
          onKey={onKey}
          onUndo={onUndo}
          dimmed={NON_TARGETS}
          disabled={busy || !playable}
          undoDisabled={busy || !canUndo(match)}
        />
      </div>
    </PlayFrame>
  )
}
