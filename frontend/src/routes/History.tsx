/**
 * The match history: one page at a time, newest first.
 *
 * Criterion 4 is "paginates and does not fetch the full history at once", and
 * the whole of it is server-side -- `GET /api/matches` has taken `limit` and
 * `offset` since #17 and returns `total` beside the page. So this asks for
 * twenty, renders twenty, and reads the count off the response. Nothing
 * accumulates: paging forward replaces the list rather than appending to it,
 * which is what keeps a long history from becoming a long request.
 *
 * The status filter is a client-side control over a server-side query -- it
 * changes the `?status=` sent, not what is done with the answer -- so filtering
 * to abandoned matches does not fetch the others either.
 *
 * Rows are links to `/history/:matchId`, which is criterion 5's deep link. #21
 * mounted that path at the real address behind a placeholder precisely so a
 * reload of it could be tested before this screen existed.
 */
import { useState } from 'react'
import { Link } from 'react-router'
import { useMatchHistory, type MatchStatus } from '../api/history'
import { Button } from '../components/Button'
import { SegmentedControl } from '../components/SegmentedControl'
import { historyRows, pageInfo } from '../matches/history'
import './History.css'

/**
 * The filter's options. `'all'` is the absence of `?status=`, not a status.
 *
 * A separate type from `MatchStatus` because the control's value is a string and
 * "no filter" has to be one of them; mapping it back to `null` at the edge keeps
 * `useMatchHistory` taking the status the API means.
 */
type FilterValue = 'all' | MatchStatus

/**
 * Three, not four.
 *
 * A fourth ("Playing") does not fit: at 402px `SegmentedControl` gives each
 * option 83px and "Abandoned" needs 98, so the label overflowed its own button
 * -- measured in a browser, because jsdom does no layout and the four-option
 * version passed every test. Three leaves ~117px each and fits.
 *
 * "Playing" is the one to lose. A match in progress is still in the unfiltered
 * list with its own badge, and the way back into one is the home screen's
 * resume card rather than a filter on a history of finished games. The
 * distinction #26 actually asks for is complete vs abandoned, and both are here.
 */
const FILTERS: { value: FilterValue; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'complete', label: 'Complete' },
  { value: 'abandoned', label: 'Abandoned' },
]

export function History() {
  const [filter, setFilter] = useState<FilterValue>('all')
  const [offset, setOffset] = useState(0)
  const page = useMatchHistory(filter === 'all' ? null : filter, offset)

  const rows = historyRows(page.data)
  const info = pageInfo(page.data)

  const onFilter = (value: FilterValue) => {
    // Back to the first page: page 3 of every match is not page 3 of the
    // abandoned ones, and keeping the offset would land on an empty page.
    setOffset(0)
    setFilter(value)
  }

  return (
    <div className="history">
      <h1 className="history__title">History</h1>

      <SegmentedControl label="Show" value={filter} options={FILTERS} onChange={onFilter} />

      {page.isPending && <p className="history__note">Reading the history&hellip;</p>}

      {page.isError && (
        <div className="history__note" role="alert">
          <p>{page.error.message}</p>
          <Button variant="secondary" onClick={() => void page.refetch()}>
            Try again
          </Button>
        </div>
      )}

      {page.data !== undefined && rows.length === 0 && (
        <p className="history__note">No matches yet. Start one and it will show up here.</p>
      )}

      {rows.length > 0 && (
        <ul className="history__list">
          {rows.map((row) => (
            <li key={row.matchId}>
              <Link
                className="history__row"
                to={`/history/${String(row.matchId)}`}
                aria-label={row.label}
              >
                <span className="history__row-main">
                  <span className="history__row-title">{row.title}</span>
                  <span className="history__row-players">{row.players}</span>
                </span>
                <span className="history__row-meta">
                  {/* The word as well as the colour: criterion 6 has to hold for
                      somebody who cannot see the styling. */}
                  <span className={`history__status history__status--${row.status}`}>
                    {row.statusText}
                  </span>
                  <span className="history__row-date">{row.date}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      {/* Only over rows. An empty history has its own message above, and a pager
          beside it would be a control over nothing. */}
      {rows.length > 0 && (
        <nav className="history__pager" aria-label="Pages">
          <Button
            variant="secondary"
            disabled={!info.hasPrev || page.isFetching}
            onClick={() => setOffset(info.prevOffset)}
          >
            Newer
          </Button>
          <span className="history__count tnum" aria-live="polite">
            {info.summary}
          </span>
          <Button
            variant="secondary"
            disabled={!info.hasNext || page.isFetching}
            onClick={() => setOffset(info.nextOffset)}
          >
            Older
          </Button>
        </nav>
      )}
    </div>
  )
}
