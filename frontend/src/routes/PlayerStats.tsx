/**
 * One player's card: every metric #27 asks for, all time and recently.
 *
 * Two requests to the same endpoint
 * ---------------------------------
 * "Lifetime and recent" is the same report over two scopes, so it is the same
 * call twice -- once unwindowed, once with `?last_matches=`. The recent column is
 * therefore the server's own calculation over fewer matches rather than an
 * average of averages, which is what lets both columns sit side by side and mean
 * the same thing. #19 has no "last N" filter of its own before this ticket; the
 * predicate was added to its SQL layer rather than approximated here, because a
 * recent average computed in the client is exactly what criterion 1 forbids.
 *
 * The column heading comes from `matches_played` inside the window, not from the
 * ten that were asked for: somebody who has played six matches sees "Last 6
 * matches", which is true, rather than "Last 10", which is not.
 *
 * Criterion 5 reads the echo, not the payload
 * -------------------------------------------
 * `PlayerStatsResponse` always carries both an `x01` and a `cricket` block
 * whatever the filter, so "does this block look empty" would hide a real zero
 * from a player who has genuinely scored nothing. Which blocks to show is decided
 * by `filter.game_type` as the response echoes it back -- the field exists so a
 * client can label a view by what was actually applied.
 *
 * Nothing on this screen divides
 * ------------------------------
 * Every figure is a field and a formatter; see `stats/stats.ts`. The one piece of
 * arithmetic in the file is the segment bar's width, which is geometry -- it sizes
 * a bar, is written to a custom property rather than to the document, and no
 * number a reader sees comes from it.
 */
import { Link, useParams, useSearchParams } from 'react-router'
import { RECENT_MATCHES, usePlayerStats } from '../api/stats'
import { Button } from '../components/Button'
import { SegmentedControl } from '../components/SegmentedControl'
import {
  CRICKET_METRICS,
  GAME_FILTERS,
  OVERALL_METRICS,
  X01_METRICS,
  gameTypeOf,
  hasThrown,
  parseGameFilter,
  segmentBars,
  showsCricket,
  showsX01,
  statRows,
  targetRows,
  windowLabel,
  type GameFilter,
  type StatRow,
} from '../stats/stats'
import './PlayerStats.css'

/** One block of the card: a heading and its rows, in a two-column table. */
function Figures({ heading, rows, recentLabel }: FiguresProps) {
  return (
    <section className="pstats__block">
      <h2 className="pstats__heading">{heading}</h2>
      <div className="pstats__grid" role="table" aria-label={heading}>
        <div className="pstats__head" role="row">
          <span role="columnheader">Metric</span>
          <span role="columnheader">All time</span>
          <span role="columnheader">{recentLabel}</span>
        </div>
        {rows.map((row) => (
          <div className="pstats__line" role="row" key={row.key} aria-label={row.ariaLabel}>
            <span className="pstats__label" role="cell">
              {row.label}
            </span>
            <span className="pstats__figure tnum" role="cell">
              {row.lifetime}
            </span>
            <span className="pstats__figure pstats__figure--recent tnum" role="cell">
              {row.recent}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}

interface FiguresProps {
  heading: string
  rows: StatRow[]
  recentLabel: string
}

export function PlayerStats() {
  const { playerId } = useParams()
  const [params, setParams] = useSearchParams()
  const game = parseGameFilter(params.get('game_type'))
  const gameType = gameTypeOf(game)

  // `useParams` gives a string; the route only matches digits in practice, but
  // NaN would become `/api/stats/players/NaN` and a 422. Zero is never a real id,
  // so it is the safe sentinel and the query below simply reports not found.
  const id = Number(playerId ?? '')
  const numericId = Number.isInteger(id) && id > 0 ? id : 0

  const lifetime = usePlayerStats(numericId, { gameType, lastMatches: null })
  const recent = usePlayerStats(numericId, { gameType, lastMatches: RECENT_MATCHES })

  const all = lifetime.data?.player
  const some = recent.data?.player
  // The filter as applied, echoed back by the response. Falls back to what was
  // asked for while the first request is still in flight.
  const applied = lifetime.data?.filter.game_type ?? gameType
  const recentHeading = windowLabel(some)

  const setGame = (value: GameFilter) => {
    const next = new URLSearchParams(params)
    if (value === 'all') next.delete('game_type')
    else next.set('game_type', value)
    setParams(next, { replace: true })
  }

  return (
    <div className="pstats">
      <p className="pstats__back">
        <Link to={{ pathname: '/stats', search: params.toString() }}>← Leaderboard</Link>
      </p>

      <h1 className="pstats__title">{all?.display_name ?? 'Player'}</h1>

      <SegmentedControl label="Game type" value={game} options={GAME_FILTERS} onChange={setGame} />

      {lifetime.isPending && <p className="pstats__note">Counting the darts&hellip;</p>}

      {lifetime.isError && (
        <div className="pstats__note" role="alert">
          <p>{lifetime.error.message}</p>
          <Button variant="secondary" onClick={() => void lifetime.refetch()}>
            Try again
          </Button>
        </div>
      )}

      {/* Criterion 3: a player with no darts gets a sentence, not a grid of em
          dashes. `darts_thrown` is one field, so this is a read and not a guess
          at whether the numbers "look" empty. */}
      {all !== undefined && !hasThrown(all) && (
        <p className="pstats__note">
          {all.display_name} has not thrown a dart
          {applied === null ? '' : ` in ${applied === 'x01' ? 'x01' : 'cricket'}`} yet. The numbers
          appear after the first leg.
        </p>
      )}

      {all !== undefined && hasThrown(all) && (
        <>
          <Figures
            heading="Overall"
            rows={statRows(OVERALL_METRICS, all, some)}
            recentLabel={recentHeading}
          />

          {showsX01(applied) && (
            <Figures
              heading="x01"
              rows={statRows(X01_METRICS, all, some)}
              recentLabel={recentHeading}
            />
          )}

          {showsCricket(applied) && (
            <>
              <Figures
                heading="Cricket"
                rows={statRows(CRICKET_METRICS, all, some)}
                recentLabel={recentHeading}
              />

              <section className="pstats__block">
                <h2 className="pstats__heading">Hit rate by target</h2>
                <div className="pstats__grid" role="table" aria-label="Hit rate by target">
                  <div className="pstats__head" role="row">
                    <span role="columnheader">Target</span>
                    <span role="columnheader">Hits</span>
                    <span role="columnheader">Rate</span>
                  </div>
                  {targetRows(all.cricket).map((row) => (
                    <div
                      className="pstats__line"
                      role="row"
                      key={row.target}
                      aria-label={row.ariaLabel}
                    >
                      <span className="pstats__label" role="cell">
                        {row.label}
                      </span>
                      <span className="pstats__figure tnum" role="cell">
                        {row.hits}
                      </span>
                      <span className="pstats__figure tnum" role="cell">
                        {row.hitRate}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}

          <section className="pstats__block">
            <h2 className="pstats__heading">Most-hit segments</h2>
            {/* Ranked, because the query returns it ranked, and sparse, because a
                segment nobody has hit has no row. A miss is one of these rows and
                is kept: it is where the dart went. */}
            <ul className="pstats__bars">
              {segmentBars(all.segments).map((bar) => (
                <li className="pstats__bar" key={bar.key} aria-label={bar.ariaLabel}>
                  <span className="pstats__bar-label tnum">{bar.label}</span>
                  <span
                    className={`pstats__bar-track${bar.isMiss ? ' pstats__bar-track--miss' : ''}`}
                    // Geometry, not a statistic. It sizes the fill and is never
                    // read as a number by anybody.
                    style={{ '--fraction': bar.fraction } as React.CSSProperties}
                  >
                    <span className="pstats__bar-fill" />
                  </span>
                  <span className="pstats__bar-count tnum">{bar.darts}</span>
                </li>
              ))}
            </ul>
            {all.segments.length === 0 && (
              <p className="pstats__note">No darts in this filter yet.</p>
            )}
          </section>
        </>
      )}
    </div>
  )
}
