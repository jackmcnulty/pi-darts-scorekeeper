/**
 * The interstitial #24 and #25 both deferred: the pause that says who won.
 *
 * Rendered over whichever board is underneath, by both of them. It is the first
 * consumer of `components/Sheet.tsx`, which has existed unused since #4 against
 * the day a real screen needed a bottom sheet.
 *
 * Why "continue" only closes it
 * -----------------------------
 * There is no "start the next leg" call, and none is needed: `services.play`
 * opened the next leg on the winning dart and applied `start_rule` itself, so by
 * the time this sheet is on screen the board behind it is *already* the new leg.
 * `legInPlay` prefers `active_leg` for exactly that reason and #26 deliberately
 * left it alone. Continue is therefore a dismissal, and criterion 2's "starts
 * the next leg with the correct starting team" is discharged by displaying
 * `active_leg.next_thrower` -- the team the server chose -- rather than by
 * alternating a counter here and hoping it agrees.
 *
 * Dismissal is keyed on the sheet, not a boolean
 * ---------------------------------------------
 * `dismissed` holds `sheetKey`, so dismissing leg 2's sheet cannot suppress leg
 * 3's. A `useEffect` is not involved and no ref is read during render -- the key
 * is derived from the payload each time, and the comparison is a string.
 *
 * The leg sheet cannot survive a reload and is not meant to. `active_leg` is
 * non-null for one response only, so the sheet appears on the winning dart; the
 * visit that finished the leg is on no later response. The match sheet does come
 * back, because `is_complete` is durable. Both are documented in `play/sheet.ts`.
 */
import { useState } from 'react'
import { Link } from 'react-router'
import { useMatchStats } from '../api/history'
import type { MatchState } from '../api/play'
import { Button } from '../components/Button'
import { Sheet } from '../components/Sheet'
import {
  checkoutDarts,
  finishingVisit,
  legLines,
  matchLines,
  playerLineLabel,
  sheetDue,
  sheetKey,
  tally,
  winnerName,
  type LegSheet,
  type PlayerLine,
} from '../play/sheet'
import './CompletionSheet.css'

function PlayerLines({ lines, caption }: { lines: PlayerLine[]; caption: string }) {
  if (lines.length === 0) return null
  return (
    <div className="csheet__stats">
      <h3 className="csheet__caption">{caption}</h3>
      <ul className="csheet__lines">
        {lines.map((line) => (
          <li key={line.playerId} className="csheet__line" aria-label={playerLineLabel(line)}>
            <span className="csheet__name">{line.name}</span>
            <span className="csheet__figure tnum">
              {line.average !== null
                ? line.average.toFixed(1)
                : line.marksPerRound !== null
                  ? line.marksPerRound.toFixed(2)
                  : '—'}
            </span>
            <span className="csheet__darts tnum">{line.dartsThrown} darts</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function MatchBody({ match, onClose }: { match: MatchState; onClose: () => void }) {
  const stats = useMatchStats(match.match_id)
  const lines = tally(match)

  return (
    <Sheet open title="Match complete" onClose={onClose}>
      {/* `Sheet`'s body stacks its children as plain blocks, so the rhythm
          between these sections is set here rather than by reaching into
          `Sheet.css` -- which other screens will use and which has no business
          knowing what #26 puts in it. */}
      <div className="csheet">
        {/* No `role="status"`. The sheet is an `aria-modal` dialog with a label,
            so opening it is already announced; a live region inside it would say
            the same thing a second time, on top of the board's own end-of-match
            notice behind it. */}
        <p className="csheet__winner">{winnerName(match, match.winner_team_id)} won the match</p>

        <ul className="csheet__tally">
          {lines.map((line) => (
            <li
              key={line.teamId}
              className={`csheet__tally-row${line.isWinner ? ' csheet__tally-row--winner' : ''}`}
              aria-label={`${line.name}, ${String(line.legsWon)} ${line.legsWon === 1 ? 'leg' : 'legs'}${line.isWinner ? ', winner' : ''}`}
            >
              <span className="csheet__name">{line.name}</span>
              <span className="csheet__legs tnum">{line.legsWon}</span>
            </li>
          ))}
        </ul>

        <PlayerLines lines={matchLines(stats.data)} caption="Match average" />

        <div className="csheet__actions">
          <Button variant="secondary" onClick={onClose}>
            Stay here
          </Button>
          <Link className="csheet__link" to={`/history/${String(match.match_id)}`}>
            See every dart
          </Link>
        </div>
      </div>
    </Sheet>
  )
}

function LegBody({
  match,
  due,
  onClose,
}: {
  match: MatchState
  due: LegSheet
  onClose: () => void
}) {
  const stats = useMatchStats(match.match_id)
  const darts = checkoutDarts(finishingVisit(due.leg))
  const next = due.next.next_thrower

  return (
    <Sheet open title={`Leg ${String(due.leg.leg_index + 1)} complete`} onClose={onClose}>
      {/* See `MatchBody` for why the sections are wrapped. */}
      <div className="csheet">
        {/* No `role="status"`, for the reason given on the match sheet above. */}
        <p className="csheet__winner">{winnerName(match, due.leg.winner_team_id)} won the leg</p>

        {darts.length > 0 && (
          <div className="csheet__checkout" aria-label={`Checkout, ${darts.join(', ')}`}>
            <span className="csheet__caption">Checkout</span>
            <span className="csheet__darts-row tnum">
              {darts.map((label, index) => (
                <span key={index} className="csheet__dart">
                  {label}
                </span>
              ))}
            </span>
          </div>
        )}

        <PlayerLines lines={legLines(stats.data, due.leg.leg_id)} caption="This leg" />

        {/* The server already chose who throws; this reports it. See the docstring. */}
        {next !== null && (
          <p className="csheet__next">
            {next.display_name} throws first in leg {due.next.leg_index + 1}
          </p>
        )}

        <div className="csheet__actions">
          <Button onClick={onClose}>Continue</Button>
        </div>
      </div>
    </Sheet>
  )
}

/**
 * The sheet, or nothing.
 *
 * The two bodies are separate components rather than two branches of this one
 * because each reads `/stats` and a hook cannot be called conditionally. Mounting
 * them only once a sheet is due is what keeps the play screen from fetching a
 * match report on every dart of every leg -- the alternative was an `enabled`
 * flag threaded through the query, which is the same thing said less directly.
 */
export function CompletionSheet({ match }: { match: MatchState }) {
  const [dismissed, setDismissed] = useState<string | null>(null)
  const due = sheetDue(match)

  if (due === null) return null
  const key = sheetKey(due)
  if (dismissed === key) return null
  const close = () => setDismissed(key)

  return due.kind === 'match' ? (
    <MatchBody match={match} onClose={close} />
  ) : (
    <LegBody match={match} due={due} onClose={close} />
  )
}
