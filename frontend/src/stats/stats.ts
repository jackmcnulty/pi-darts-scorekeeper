/**
 * Everything the stat card and the leaderboard show, as pure functions.
 *
 * The same split as `setup/config.ts`, `play/x01.ts` and `matches/history.ts`:
 * decisions here, rendering in the components, so the tests can enumerate the
 * awkward cases -- a null average, a zero count, a player who has never thrown
 * -- against fixtures instead of driving them through the UI.
 *
 * #27's first criterion is the shape of this file
 * ----------------------------------------------
 * > Every number on screen maps directly to a field in the `/api/stats`
 * > response. Stats are computed in SQL, never in the client.
 *
 * So a `Metric` is a **label, a field and a formatter**, and nothing else. There
 * is no arithmetic anywhere below: `toFixed` rounds for display, `String`
 * stringifies, and every value returned traces to one field of one response. A
 * checkout percentage is read from `checkout_percentage`, not divided out of
 * `checkouts_hit` and `checkout_attempts` -- both of which are right there, which
 * is exactly why the criterion is worth a test.
 *
 * `stats.test.ts` walks every metric in every table over three payloads (real
 * values, zeroes, nulls), and `PlayerStats.test.tsx` walks the rendered DOM and
 * proves each number in it is reproducible from the payload by formatting alone.
 *
 * Nulls are not a division guard
 * ------------------------------
 * The server sends `null` for every average it has no darts to compute -- an
 * average of no darts does not exist, and 0 would read as a bad one. So
 * criterion 3's "not NaN" is satisfied by rendering null as an em dash, never by
 * checking a denominator. There are no denominators here. If a NaN ever reaches a
 * screen, something in the render path did arithmetic it was not supposed to.
 */
import type {
  CricketStats,
  GameType,
  LeaderboardRow,
  PlayerStats,
  Segment,
  X01Stats,
} from '../api/stats'
import { targetLabel } from '../play/cricket'

/**
 * The game-type filter's value, where "all" is the absence of `?game_type=`.
 *
 * A separate type from `GameType` because the control's value is a string and
 * "no filter" has to be one of them; mapping it back to null at the edge keeps
 * the hooks taking the filter the API means. `api/history.ts` makes the same
 * bargain with `?status=`.
 */
export type GameFilter = 'all' | GameType

/** The three options, and three is what `SegmentedControl` fits at 402px. */
export const GAME_FILTERS: readonly { value: GameFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'x01', label: 'x01' },
  { value: 'cricket', label: 'Cricket' },
]

/**
 * Whether the view is windowed to recent matches.
 *
 * Two values rather than a free number: a screen offers "all time" or "recent",
 * and how many matches recent is belongs to `RECENT_MATCHES`, not to the address
 * bar. A hand-edited `?span=7` would be a filter nothing in the UI can represent
 * and no control could show as active.
 */
export type SpanFilter = 'all' | 'recent'

export const SPAN_FILTERS: readonly { value: SpanFilter; label: string }[] = [
  { value: 'all', label: 'All time' },
  { value: 'recent', label: 'Recent' },
]

/**
 * Read the game-type filter out of a query string, which is criterion 2.
 *
 * Anything unrecognised is "all" rather than an error. A stats screen is a place
 * you land from a shared link or a stale bookmark, and the useful answer to
 * `?game_type=x02` is the unfiltered table -- not a broken screen, and not a 422
 * from a request that should never have been sent.
 */
export function parseGameFilter(raw: string | null): GameFilter {
  return raw === 'x01' || raw === 'cricket' ? raw : 'all'
}

export function parseSpanFilter(raw: string | null): SpanFilter {
  return raw === 'recent' ? 'recent' : 'all'
}

/** The filter as the API takes it: null for "do not narrow by this". */
export function gameTypeOf(filter: GameFilter): GameType | null {
  return filter === 'all' ? null : filter
}

/**
 * Why the leaderboard is empty, in one sentence.
 *
 * Built here rather than interpolated into JSX so that it is one text node: a
 * sentence assembled out of `{minDarts}` and a conditional clause renders as four
 * neighbouring nodes, which reads the same to a person and is unfindable to both
 * a test and a screen reader.
 *
 * The threshold is named because an empty table is otherwise indistinguishable
 * from a broken one. The server hides players under `min_darts`, so a household
 * that has just started has nobody on the table and deserves to be told why
 * rather than left wondering -- and `min_darts` comes from the response, so the
 * sentence describes the threshold that was actually applied.
 */
export function emptyTableMessage(minDarts: number, span: SpanFilter, window: number): string {
  const scope = span === 'recent' ? ` in their last ${String(window)} matches` : ''
  return `Nobody has thrown ${String(minDarts)} x01 darts${scope} yet. Play a few legs and the table fills up.`
}

/**
 * What a figure with nothing behind it looks like.
 *
 * An em dash rather than "0", "-" or an empty cell: zero is a fact this app
 * reports often and truthfully, so a missing average must not look like one.
 */
export const ABSENT = '—'

/** An average, to two decimal places. Criterion 4. */
export function average(value: number | null | undefined): string {
  return value === null || value === undefined ? ABSENT : value.toFixed(2)
}

/** A percentage, to one decimal place, with its sign. Criterion 4. */
export function percentage(value: number | null | undefined): string {
  return value === null || value === undefined ? ABSENT : `${value.toFixed(1)}%`
}

/** A count. Already whole, so it is stringified rather than rounded. */
export function whole(value: number | null | undefined): string {
  return value === null || value === undefined ? ABSENT : String(value)
}

/** "3 of 5", for a tally against its opportunities. Two fields, no arithmetic. */
export function tally(won: number | null | undefined, played: number | null | undefined): string {
  if (won === null || won === undefined || played === null || played === undefined) return ABSENT
  return `${String(won)} of ${String(played)}`
}

/** "1 match" / "6 matches". */
export function matches(count: number): string {
  return `${String(count)} ${count === 1 ? 'match' : 'matches'}`
}

/** "1 dart" / "42 darts". */
export function darts(count: number): string {
  return `${String(count)} ${count === 1 ? 'dart' : 'darts'}`
}

/**
 * One line of a stat card: what it is called, and how to read it off a report.
 *
 * `read` is deliberately narrow. It takes a whole `PlayerStats` and returns a
 * *string*, so the only thing a metric can do is pick a field and format it --
 * there is nowhere to put a calculation even if somebody wanted one, and the
 * table is the enumeration the tests walk.
 */
export interface Metric {
  key: string
  label: string
  read: (stats: PlayerStats) => string
}

/** Whole-history figures, over darts of both game types. */
export const OVERALL_METRICS: readonly Metric[] = [
  { key: 'darts_thrown', label: 'Darts thrown', read: (p) => whole(p.darts_thrown) },
  { key: 'legs_won', label: 'Legs won', read: (p) => tally(p.legs_won, p.legs_played) },
  { key: 'matches_won', label: 'Matches won', read: (p) => tally(p.matches_won, p.matches_played) },
]

/**
 * x01 scoring, always over x01 darts.
 *
 * The bands are cumulative and count visits, not darts -- a 180 is also a 140+
 * -- which is the server's definition and the reason they are four separate
 * fields rather than one derived from another.
 */
export const X01_METRICS: readonly Metric[] = [
  {
    key: 'three_dart_average',
    label: '3-dart average',
    read: (p) => average(p.x01.three_dart_average),
  },
  {
    key: 'first_nine_average',
    label: 'First 9 average',
    read: (p) => average(p.x01.first_nine_average),
  },
  { key: 'average_visit', label: 'Average visit', read: (p) => average(p.x01.average_visit) },
  { key: 'highest_visit', label: 'Highest visit', read: (p) => whole(p.x01.highest_visit) },
  { key: 'one_eighties', label: '180s', read: (p) => whole(p.x01.bands.one_eighties) },
  { key: 'one_forty_plus', label: '140+', read: (p) => whole(p.x01.bands.one_forty_plus) },
  { key: 'hundred_plus', label: '100+', read: (p) => whole(p.x01.bands.hundred_plus) },
  { key: 'sixty_plus', label: '60+', read: (p) => whole(p.x01.bands.sixty_plus) },
  {
    key: 'checkout_percentage',
    label: 'Checkout %',
    read: (p) => percentage(p.x01.checkout_percentage),
  },
  { key: 'best_checkout', label: 'Best checkout', read: (p) => whole(p.x01.best_checkout) },
  { key: 'x01_darts', label: 'x01 darts', read: (p) => whole(p.x01.darts_thrown) },
]

/**
 * Cricket scoring, always over cricket darts.
 *
 * MPR is an average and gets two decimals like the others. The per-target hit
 * rates are a table of their own rather than eight more rows here -- see
 * `targetRows`.
 *
 * `marks`, `darts_on_target` and `wasted_darts` are on the payload and are
 * deliberately not shown: #27's scope names MPR and the per-target hit rate, and
 * the ticket is explicit that this screen renders what was asked for rather than
 * everything available.
 */
export const CRICKET_METRICS: readonly Metric[] = [
  {
    key: 'marks_per_round',
    label: 'Marks per round',
    read: (p) => average(p.cricket.marks_per_round),
  },
  { key: 'cricket_darts', label: 'Cricket darts', read: (p) => whole(p.cricket.darts_thrown) },
]

/**
 * Which blocks a filtered card shows, which is criterion 5.
 *
 * Driven by the filter the response *echoes back*, not by sniffing whether a
 * block looks empty. `PlayerStatsResponse` always carries both `x01` and
 * `cricket` whatever was asked for, so "does this look empty" would hide a real
 * zero -- and the echo exists precisely so a client can label a view by what was
 * actually applied.
 */
export function showsX01(gameType: GameType | null): boolean {
  return gameType !== 'cricket'
}

export function showsCricket(gameType: GameType | null): boolean {
  return gameType !== 'x01'
}

export interface StatRow {
  key: string
  label: string
  /** Over the whole history, within the game-type filter. */
  lifetime: string
  /** Over the recent window, or `ABSENT` while it is still loading. */
  recent: string
  /** Spelled out: adjacent spans of a table row otherwise run together. */
  ariaLabel: string
}

/**
 * One row per metric, with a column for each scope.
 *
 * Both columns come from the same endpoint asked twice, so a recent average is
 * the same calculation over fewer matches rather than an approximation of one.
 * An absent report -- still loading, or errored -- reads as an em dash rather
 * than a zero.
 */
export function statRows(
  metrics: readonly Metric[],
  lifetime: PlayerStats | undefined,
  recent: PlayerStats | undefined,
): StatRow[] {
  return metrics.map((metric) => {
    const all = lifetime === undefined ? ABSENT : metric.read(lifetime)
    const some = recent === undefined ? ABSENT : metric.read(recent)
    return {
      key: metric.key,
      label: metric.label,
      lifetime: all,
      recent: some,
      ariaLabel: `${metric.label}: ${all} all time, ${some} recently`,
    }
  })
}

export interface TargetRow {
  target: number
  /** "20", or "Bull" for 25. */
  label: string
  hits: string
  hitRate: string
  ariaLabel: string
}

/**
 * Per-target hit rate, one row per cricket target.
 *
 * The rate is `hit_rate` read straight off the response -- not `hits` over
 * anything. Its denominator is every cricket dart in scope, misses included,
 * which is a definition the client has no way to reconstruct and no business
 * trying to.
 *
 * `targetLabel` is #25's, reused rather than respelled: there is one place that
 * decides 25 is called "Bull".
 */
export function targetRows(cricket: CricketStats): TargetRow[] {
  return cricket.targets.map((target) => ({
    target: target.target,
    label: targetLabel(target.target),
    hits: whole(target.hits),
    hitRate: percentage(target.hit_rate),
    ariaLabel: `${targetLabel(target.target)}: ${whole(target.hits)} hits, ${percentage(
      target.hit_rate,
    )} hit rate`,
  }))
}

/** How many bars the segment visual draws. See `segmentBars`. */
export const SEGMENT_BARS = 12

export interface SegmentBar {
  key: string
  /** The server's own name for the segment: "T20", "BULL", "MISS". */
  label: string
  /** How many darts landed there, as text. */
  darts: string
  /**
   * Bar length as a fraction of the most-hit segment, 0 to 1.
   *
   * Geometry, not a statistic: it sizes a bar and is never displayed, so no
   * number a reader sees comes from it. That is the line criterion 1 draws --
   * about numbers on screen, not about pixels.
   */
  fraction: number
  /** True for segment 0, which is not on the board at all. */
  isMiss: boolean
  ariaLabel: string
}

/**
 * Where the darts actually land, most-hit first.
 *
 * A ranked bar list rather than a dartboard, because that is the shape of the
 * data: `segment_frequency` is a `GROUP BY` ordered by `count(*) DESC`, so it
 * arrives already ranked and **sparse** -- a segment nobody has ever hit has no
 * row at all. A board map would have to invent the missing rows as zeroes and
 * then draw sixty-two mostly empty cells on a 402px phone; a ranked list of what
 * was hit says the same thing in the order the query already established.
 *
 * Absence therefore means zero, and needs no filling in: a segment with no row
 * is a segment with no bar.
 *
 * A miss is a row like any other -- segment 0, multiplier 0 -- and is kept
 * rather than dropped, because "where darts actually land" includes off the
 * board, and silently discarding the misses would flatter every player. It is
 * marked so the visual can say which bar is not a segment.
 *
 * Truncated to `SEGMENT_BARS`, since the tail of a long history is a hundred
 * segments hit once each. The heading says "most-hit" rather than claiming to be
 * the whole board.
 */
export function segmentBars(
  segments: readonly Segment[],
  limit: number = SEGMENT_BARS,
): SegmentBar[] {
  const shown = segments.slice(0, limit)
  // The busiest segment sets the scale. Read with `Math.max` rather than trusted
  // from position 0: the ordering is the server's promise, and a visual that
  // silently drew every bar over-long if it ever changed would be a poor way to
  // find out.
  const most = shown.reduce((best, entry) => Math.max(best, entry.darts), 0)
  return shown.map((entry) => ({
    key: `${String(entry.segment)}x${String(entry.multiplier)}`,
    label: entry.label,
    darts: whole(entry.darts),
    fraction: most > 0 ? entry.darts / most : 0,
    isMiss: entry.segment === 0,
    ariaLabel: `${entry.label}: ${darts(entry.darts)}`,
  }))
}

/**
 * One row of the leaderboard, holding exactly what the row shows.
 *
 * `highest_visit`, `checkout_percentage` and `best_checkout` are on the response
 * and are deliberately not here. Measured in a browser at 402px, a row has 197px
 * for its detail line and the four-stat version needed 242 -- so the checkout
 * percentage was rendering as a clipped "100.0…" with its own label cut off,
 * which is worse than absent. Volume and maximums fit in 119px and are the two
 * that give a ranking position context; the checkout figures are on the player's
 * card, where they have room to be labelled.
 */
export interface RankRow {
  playerId: number
  name: string
  average: string
  darts: string
  oneEighties: string
  ariaLabel: string
}

/**
 * The leaderboard, in the order the server ranked it.
 *
 * No rank number is computed. The screen renders these in an `<ol>`, so the
 * position *is* the rank -- conveyed to a screen reader by the list and drawn
 * for everyone else by a CSS counter. `index + 1` would be the obvious thing to
 * put in the label and would be the one number on the screen that no field of
 * the response backs.
 */
export function rankRows(rows: readonly LeaderboardRow[]): RankRow[] {
  return rows.map((row) => ({
    playerId: row.player_id,
    name: row.display_name,
    average: average(row.three_dart_average),
    darts: whole(row.darts_thrown),
    oneEighties: whole(row.one_eighties),
    ariaLabel: `${row.display_name}: ${average(row.three_dart_average)} three-dart average, ${darts(
      row.darts_thrown,
    )} thrown, ${row.one_eighties} maximums`,
  }))
}

/**
 * What the recent column is honestly called.
 *
 * From `matches_played` within the window, not from the window that was asked
 * for: a request for the last ten matches by somebody who has played six covers
 * six, and "Last 6 matches" is true where "Last 10 matches" would not be. The
 * API echoes the request so a client *can* say what it asked; this says what it
 * got, which is the more useful of the two on a card.
 */
export function windowLabel(stats: PlayerStats | undefined): string {
  if (stats === undefined) return 'Recent'
  return `Last ${matches(stats.matches_played)}`
}

/**
 * Whether a report has anything in it, which decides the empty state.
 *
 * `darts_thrown` over both game types, so a player with only cricket darts is
 * not "empty" on an unfiltered card. Criterion 3's case is a player who has
 * thrown nothing at all, and this is that, read from one field.
 */
export function hasThrown(stats: PlayerStats | undefined): boolean {
  return stats !== undefined && stats.darts_thrown > 0
}

/** Whether the x01 block has anything to say, for a card filtered to cricket. */
export function hasX01(x01: X01Stats): boolean {
  return x01.darts_thrown > 0
}

/** Whether the cricket block has anything to say. */
export function hasCricket(cricket: CricketStats): boolean {
  return cricket.darts_thrown > 0
}
