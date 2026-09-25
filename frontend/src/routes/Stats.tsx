/**
 * The leaderboard: everybody ranked, with a game-type filter.
 *
 * Both filters live in the query string, which is criterion 2 -- so a filtered
 * view is a link somebody can send, and a refresh lands on the same table.
 * `useSearchParams` rather than `useState` for exactly that reason; #26's history
 * filter is `useState` and is deliberately not linkable, which is the difference
 * between the two criteria and not an inconsistency to copy.
 *
 * The rank is not a number this screen computes
 * ---------------------------------------------
 * Rows arrive in the server's ranked order and are rendered in an `<ol>`, so the
 * position *is* the rank: conveyed to a screen reader by the list itself and drawn
 * for everybody else by a CSS counter. `index + 1` would have been the one number
 * on the screen that no field of the response backs, which is precisely what
 * criterion 1 forbids.
 *
 * Two empty states, because they are two different facts
 * ------------------------------------------------------
 * "Nobody has qualified yet" is a threshold that has not been met -- the server
 * hides players under `min_darts`, so a new player legitimately has no row. That
 * is not the same as having no players, and it is not the same as the recent
 * window finding nobody with recent form. The message names the threshold from
 * the response, so it explains itself rather than looking like a bug.
 */
import { Link, useSearchParams } from 'react-router'
import { RECENT_MATCHES, useLeaderboard } from '../api/stats'
import { Button } from '../components/Button'
import { SegmentedControl } from '../components/SegmentedControl'
import {
  GAME_FILTERS,
  SPAN_FILTERS,
  emptyTableMessage,
  gameTypeOf,
  parseGameFilter,
  parseSpanFilter,
  rankRows,
  type GameFilter,
  type SpanFilter,
} from '../stats/stats'
import './Stats.css'

export function Stats() {
  const [params, setParams] = useSearchParams()
  const game = parseGameFilter(params.get('game_type'))
  const span = parseSpanFilter(params.get('span'))

  const table = useLeaderboard({
    gameType: gameTypeOf(game),
    lastMatches: span === 'recent' ? RECENT_MATCHES : null,
  })
  const rows = rankRows(table.data?.rows ?? [])

  /**
   * Write one filter back to the address bar.
   *
   * `replace` so that flipping a filter three times does not put three entries
   * in the back stack -- the back button should leave the screen, not undo a
   * tap. The default value is *removed* rather than written, so the plain
   * `/stats` address stays clean and is what a shared link looks like.
   */
  const set = (key: string, value: string, isDefault: boolean) => {
    const next = new URLSearchParams(params)
    if (isDefault) next.delete(key)
    else next.set(key, value)
    setParams(next, { replace: true })
  }

  return (
    <div className="stats">
      <h1 className="stats__title">Stats</h1>

      <SegmentedControl
        label="Game type"
        value={game}
        options={GAME_FILTERS}
        onChange={(value: GameFilter) => set('game_type', value, value === 'all')}
      />
      <SegmentedControl
        label="Span"
        value={span}
        options={SPAN_FILTERS}
        onChange={(value: SpanFilter) => set('span', value, value === 'all')}
      />

      {span === 'recent' && (
        <p className="stats__note">
          Each player over their own last {RECENT_MATCHES} matches, so the column compares like with
          like.
        </p>
      )}

      {table.isPending && <p className="stats__note">Counting the darts&hellip;</p>}

      {table.isError && (
        <div className="stats__note" role="alert">
          <p>{table.error.message}</p>
          <Button variant="secondary" onClick={() => void table.refetch()}>
            Try again
          </Button>
        </div>
      )}

      {table.data !== undefined && rows.length === 0 && (
        <p className="stats__note">
          {emptyTableMessage(table.data.min_darts, span, RECENT_MATCHES)}
        </p>
      )}

      {rows.length > 0 && (
        <ol className="stats__table">
          {rows.map((row) => (
            <li key={row.playerId} className="stats__rank">
              <Link
                className="stats__row"
                // The filter travels with the link, so a card opened from a
                // cricket table is a cricket card.
                to={{ pathname: `/stats/${String(row.playerId)}`, search: params.toString() }}
                aria-label={row.ariaLabel}
              >
                <span className="stats__row-main">
                  <span className="stats__name">{row.name}</span>
                  <span className="stats__detail tnum">
                    {row.darts} darts · {row.oneEighties} × 180
                  </span>
                </span>
                <span className="stats__average tnum">{row.average}</span>
              </Link>
            </li>
          ))}
        </ol>
      )}

      {rows.length > 0 && <p className="stats__legend">Ranked by 3-dart average.</p>}
    </div>
  )
}
