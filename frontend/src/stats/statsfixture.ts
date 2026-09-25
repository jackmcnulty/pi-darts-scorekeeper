/**
 * Statistics payloads to render against, built from the real generated types.
 *
 * The same bargain as `play/statefixture.ts` and `matches/historyfixture.ts`:
 * every return type is the generated response and nothing is `as` cast, so a
 * fixture that has drifted from the served schema stops typechecking rather than
 * quietly describing something the Pi no longer sends. That is the only guard a
 * hand-written fixture can have, and it is the reason #27's `label` on
 * `SegmentResponse` could not be forgotten here.
 *
 * Three shapes matter, because they are the three cases criteria 3 and 4 are
 * about:
 *
 * * `playerStats()` -- real values, every field distinct so a test can tell which
 *   one a screen rendered. Deliberately not round: `57.234` formats to `57.23`
 *   only if somebody rounded it to two places, and `33.33` to `33.3` only at one.
 * * `zeroStats()` -- a player who has thrown darts and scored nothing. Counts are
 *   0 and averages exist, because 0 is a fact.
 * * `emptyStats()` -- a player who has never thrown. Every count 0, every average
 *   `null`, `segments` absent entirely. This is what the server really sends, and
 *   what criterion 3's empty state is about.
 *
 * Not a `*.test.ts`, so like the other two fixtures it is named explicitly in
 * `tsconfig.test.json`, excluded from `tsconfig.app.json`, and excluded from
 * coverage: it is scaffolding, not app code.
 */
import type {
  CricketStats,
  GameType,
  Leaderboard,
  LeaderboardRow,
  PlayerReport,
  PlayerStats,
  Segment,
  TargetStats,
  X01Stats,
} from '../api/stats'

export const JACK = 1
export const DAD = 2

/** The seven cricket targets, in the order the server returns them. */
const TARGETS: readonly number[] = [20, 19, 18, 17, 16, 15, 25]

export interface X01Options {
  threeDartAverage?: number | null
  firstNineAverage?: number | null
  averageVisit?: number | null
  highestVisit?: number | null
  checkoutPercentage?: number | null
  bestCheckout?: number | null
  dartsThrown?: number
}

/**
 * x01 scoring with every figure distinct, so a test can tell them apart.
 *
 * The averages carry more precision than they are displayed with, which is what
 * makes the formatting criterion testable: `57.234` is `57.23` at two places and
 * would be `57.2` at one.
 *
 * **Every authoritative figure here disagrees with what its ingredients would
 * compute**, on purpose. `checkouts_hit / checkout_attempts` is 29.4% where
 * `checkout_percentage` says 33.3%; `3 * points_scored / darts_thrown` is 57.13
 * where `three_dart_average` says 57.23. That makes criterion 1 testable by *any*
 * test that renders this fixture: a screen that divided would put a number on
 * the page that no field of the payload backs, and the DOM walk in
 * `PlayerStats.test.tsx` would see it. A fixture whose ingredients happened to
 * agree would let exactly that bug through, which is how this one started out.
 */
export function x01(options: X01Options = {}): X01Stats {
  const {
    threeDartAverage = 57.234,
    firstNineAverage = 62.567,
    averageVisit = 19.078,
    highestVisit = 140,
    checkoutPercentage = 33.333,
    bestCheckout = 121,
    dartsThrown = 450,
  } = options
  return {
    darts_thrown: dartsThrown,
    visits: 150,
    points_scored: 8570,
    three_dart_average: threeDartAverage,
    first_nine_average: firstNineAverage,
    first_nine_darts: 90,
    highest_visit: highestVisit,
    average_visit: averageVisit,
    bands: { one_eighties: 3, one_forty_plus: 11, hundred_plus: 29, sixty_plus: 74 },
    // 5 of 17 is 29.4%, deliberately not the 33.3% `checkout_percentage` states.
    checkout_attempts: 17,
    checkouts_hit: 5,
    checkout_percentage: checkoutPercentage,
    best_checkout: bestCheckout,
  }
}

/** Every average null and every count zero: nobody has thrown an x01 dart. */
export function emptyX01(): X01Stats {
  return {
    darts_thrown: 0,
    visits: 0,
    points_scored: 0,
    three_dart_average: null,
    first_nine_average: null,
    first_nine_darts: 0,
    highest_visit: null,
    average_visit: null,
    bands: { one_eighties: 0, one_forty_plus: 0, hundred_plus: 0, sixty_plus: 0 },
    checkout_attempts: 0,
    checkouts_hit: 0,
    checkout_percentage: null,
    best_checkout: null,
  }
}

function target(value: number, hits: number, hitRate: number | null): TargetStats {
  return { target: value, hits, marks: hits, hit_rate: hitRate }
}

export interface CricketOptions {
  marksPerRound?: number | null
  dartsThrown?: number
  /** Per-target hit rates, in `TARGETS` order. Null for "no darts to rate". */
  hitRates?: readonly (number | null)[]
}

export function cricket(options: CricketOptions = {}): CricketStats {
  const { marksPerRound = 2.456, dartsThrown = 210 } = options
  // Distinct, awkward rates: one over 10%, one under, one exactly on a rounding
  // boundary, and one null.
  const hitRates = options.hitRates ?? [24.761, 18.095, 12.5, 9.047, 7.15, 4.285, null]
  return {
    darts_thrown: dartsThrown,
    // 3 * 210 / 210 is 3.00, deliberately not the 2.46 `marks_per_round` states.
    marks: 210,
    darts_on_target: 121,
    wasted_darts: 12,
    marks_per_round: marksPerRound,
    targets: TARGETS.map((value, index) => target(value, 30 - index * 4, hitRates[index] ?? null)),
  }
}

export function emptyCricket(): CricketStats {
  return {
    darts_thrown: 0,
    marks: 0,
    darts_on_target: 0,
    wasted_darts: 0,
    marks_per_round: null,
    // The server sends all seven targets with nothing on them, not an empty list.
    targets: TARGETS.map((value) => target(value, 0, null)),
  }
}

/**
 * Segment frequency as the query returns it: sparse, and most-hit first.
 *
 * Sparse because `segment_frequency` is a `GROUP BY` -- a segment nobody has hit
 * has no row -- and ordered by `count(*) DESC` because that is the query's own
 * `ORDER BY`. A fixture in board order would let a visual that ignored the
 * ordering pass, so this one is deliberately ranked and full of holes. The miss
 * is included, because the server sends one.
 */
export function segments(): Segment[] {
  return [
    { segment: 20, multiplier: 3, darts: 64, label: 'T20' },
    { segment: 20, multiplier: 1, darts: 51, label: '20' },
    { segment: 0, multiplier: 0, darts: 37, label: 'MISS' },
    { segment: 5, multiplier: 1, darts: 26, label: '5' },
    { segment: 1, multiplier: 1, darts: 22, label: '1' },
    { segment: 19, multiplier: 3, darts: 18, label: 'T19' },
    { segment: 25, multiplier: 2, darts: 9, label: 'BULL' },
    { segment: 25, multiplier: 1, darts: 7, label: '25' },
    { segment: 16, multiplier: 2, darts: 4, label: 'D16' },
    { segment: 12, multiplier: 1, darts: 1, label: '12' },
  ]
}

export interface PlayerOptions {
  playerId?: number
  name?: string
  isArchived?: boolean
  dartsThrown?: number
  legsPlayed?: number
  legsWon?: number
  matchesPlayed?: number
  matchesWon?: number
  x01?: X01Stats
  cricket?: CricketStats
  segments?: Segment[]
}

/** A player with a full history in both game types. */
export function playerStats(options: PlayerOptions = {}): PlayerStats {
  return {
    player_id: options.playerId ?? JACK,
    display_name: options.name ?? 'Jack',
    is_archived: options.isArchived ?? false,
    darts_thrown: options.dartsThrown ?? 660,
    legs_played: options.legsPlayed ?? 38,
    legs_won: options.legsWon ?? 21,
    matches_played: options.matchesPlayed ?? 14,
    matches_won: options.matchesWon ?? 8,
    x01: options.x01 ?? x01(),
    cricket: options.cricket ?? cricket(),
    segments: options.segments ?? segments(),
  }
}

/**
 * A player who has thrown and scored nothing: counts are 0, averages exist.
 *
 * The distinction criterion 3 turns on. This player is not empty -- they have
 * thrown 45 darts -- so a card must show their zeroes rather than an empty
 * state, and a 0.00 average is the truth about them.
 */
export function zeroStats(options: PlayerOptions = {}): PlayerStats {
  return playerStats({
    name: 'Newcomer',
    dartsThrown: 45,
    legsPlayed: 2,
    legsWon: 0,
    matchesPlayed: 1,
    matchesWon: 0,
    x01: x01({
      threeDartAverage: 0,
      firstNineAverage: 0,
      averageVisit: 0,
      highestVisit: 0,
      checkoutPercentage: 0,
      bestCheckout: null,
      dartsThrown: 45,
    }),
    cricket: emptyCricket(),
    segments: [{ segment: 0, multiplier: 0, darts: 45, label: 'MISS' }],
    ...options,
  })
}

/** A player who has never thrown a dart. Every average null, no segments. */
export function emptyStats(options: PlayerOptions = {}): PlayerStats {
  return playerStats({
    name: 'Nobody',
    dartsThrown: 0,
    legsPlayed: 0,
    legsWon: 0,
    matchesPlayed: 0,
    matchesWon: 0,
    x01: emptyX01(),
    cricket: emptyCricket(),
    segments: [],
    ...options,
  })
}

export interface ReportOptions {
  gameType?: GameType | null
  lastMatches?: number | null
  player?: PlayerStats
}

/** A `PlayerReportResponse`, echoing the filter it was asked with. */
export function playerReport(options: ReportOptions = {}): PlayerReport {
  return {
    filter: {
      game_type: options.gameType ?? null,
      variant: null,
      since: null,
      match_id: null,
      last_matches: options.lastMatches ?? null,
    },
    player: options.player ?? playerStats(),
  }
}

export interface RankOptions {
  playerId: number
  name: string
  average?: number | null
  dartsThrown?: number
  highestVisit?: number | null
  oneEighties?: number
  checkoutPercentage?: number | null
  bestCheckout?: number | null
}

/**
 * One ranked row.
 *
 * Destructured defaults rather than `??`, because every nullable field here has
 * to be settable *to null*: a player with no average is the case the leaderboard's
 * em dash exists for, and `options.average ?? 57.234` would quietly hand it a
 * real average instead. A destructuring default fires only on `undefined`, which
 * is the distinction the fixture needs.
 */
export function rankRow(options: RankOptions): LeaderboardRow {
  const {
    average = 57.234,
    dartsThrown = 450,
    highestVisit = 140,
    oneEighties = 3,
    checkoutPercentage = 33.333,
    bestCheckout = 121,
  } = options
  return {
    player_id: options.playerId,
    display_name: options.name,
    darts_thrown: dartsThrown,
    three_dart_average: average,
    highest_visit: highestVisit,
    one_eighties: oneEighties,
    checkout_attempts: 18,
    checkouts_hit: 6,
    checkout_percentage: checkoutPercentage,
    best_checkout: bestCheckout,
  }
}

/** Two players, better average first, as the server ranks them. */
export function ranked(): LeaderboardRow[] {
  return [
    rankRow({ playerId: JACK, name: 'Jack', average: 57.234 }),
    rankRow({
      playerId: DAD,
      name: 'Dad',
      average: 48.916,
      dartsThrown: 390,
      highestVisit: 121,
      oneEighties: 1,
      checkoutPercentage: 22.222,
      bestCheckout: 76,
    }),
  ]
}

export interface LeaderboardOptions {
  rows?: LeaderboardRow[]
  minDarts?: number
  gameType?: GameType | null
  lastMatches?: number | null
}

export function leaderboard(options: LeaderboardOptions = {}): Leaderboard {
  return {
    filter: {
      game_type: options.gameType ?? null,
      variant: null,
      since: null,
      match_id: null,
      last_matches: options.lastMatches ?? null,
    },
    min_darts: options.minDarts ?? 50,
    ranked_by: 'three_dart_average',
    rows: options.rows ?? ranked(),
  }
}
