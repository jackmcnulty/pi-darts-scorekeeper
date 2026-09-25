/**
 * Reading statistics: one player, and the ranking.
 *
 * Both are reads of #19's endpoints, and neither computes anything. That is
 * #27's first criterion and it starts here: these hooks hand the response
 * through untouched, and `stats/stats.ts` chooses and formats fields out of it.
 * If a number a screen shows is not in one of these payloads, it belongs in
 * #19's SQL rather than in a `.map` on the way past.
 *
 * `useMatchStats` is not here. It is #26's, lives in `api/history.ts` beside the
 * other per-match reads, and already serves `/api/stats/matches/{id}`; a second
 * hook for the same endpoint would be a second cache entry for one answer.
 *
 * Keys
 * ----
 * Under a `'stats'` root of their own rather than under `MATCHES_KEY`. A report
 * is invalidated by a dart being recorded, not by a match being created, and
 * nesting these under the matches key would make #23's create-match
 * invalidation refetch every stat screen the app has ever opened. Recording a
 * dart is #24's concern and already invalidates what it must; a stats screen is
 * read fresh when it is opened.
 *
 * The filter is part of the key, because two filters are two different answers
 * to two different questions -- not one answer that changed.
 */
import { useQuery } from '@tanstack/react-query'
import type { UseQueryResult } from '@tanstack/react-query'
import { api, unwrap } from './client'
import type { components } from './schema'

export type PlayerReport = components['schemas']['PlayerReportResponse']
export type PlayerStats = components['schemas']['PlayerStatsResponse']
export type Leaderboard = components['schemas']['LeaderboardResponse']
export type LeaderboardRow = components['schemas']['LeaderboardRowResponse']
export type X01Stats = components['schemas']['X01Response']
export type CricketStats = components['schemas']['CricketResponse']
export type TargetStats = components['schemas']['TargetResponse']
export type Segment = components['schemas']['SegmentResponse']
export type GameType = components['schemas']['GameType']

export const STATS_KEY = ['stats'] as const

/**
 * The window #27's "recent" column means, in matches.
 *
 * Ten because that is a handful of evenings rather than a season -- long enough
 * that one bad leg does not define it, short enough to still be *recent*. The
 * API has no default of its own: an absent `?last_matches=` is lifetime, so this
 * is the client's choice about what to ask for and not a number the server
 * imposes. See `backend/darts/api/stats.py`.
 */
export const RECENT_MATCHES = 10

/**
 * What both screens narrow by, as the query string carries it.
 *
 * `gameType` is null for "all", which is the *absence* of `?game_type=` rather
 * than a value -- the same bargain `api/history.ts` makes with `?status=`.
 */
export interface StatsFilter {
  gameType: GameType | null
  /** Matches to look back over, or null for lifetime. */
  lastMatches: number | null
}

export const LIFETIME: StatsFilter = { gameType: null, lastMatches: null }

/** The query as the API takes it: absent keys rather than nulls. */
function query(filter: StatsFilter): Record<string, string | number> {
  return {
    ...(filter.gameType === null ? {} : { game_type: filter.gameType }),
    ...(filter.lastMatches === null ? {} : { last_matches: filter.lastMatches }),
  }
}

export function playerStatsKey(playerId: number, filter: StatsFilter) {
  return [...STATS_KEY, 'player', playerId, filter] as const
}

export function leaderboardKey(filter: StatsFilter) {
  return [...STATS_KEY, 'leaderboard', filter] as const
}

/**
 * One player's report, within one filter.
 *
 * Asked twice by the stat card -- once without a window and once with one -- so
 * that "lifetime" and "recent" are the same numbers over two scopes rather than
 * one scope and an approximation. Two cache entries, because they are two
 * questions.
 */
export function usePlayerStats(
  playerId: number,
  filter: StatsFilter = LIFETIME,
): UseQueryResult<PlayerReport> {
  return useQuery({
    queryKey: playerStatsKey(playerId, filter),
    queryFn: () =>
      unwrap(
        api.GET('/api/stats/players/{player_id}', {
          params: { path: { player_id: playerId }, query: query(filter) },
        }),
      ),
  })
}

/**
 * The ranking. With a window it is a form table: every player over their own
 * last N matches, which is why the column stays comparable down the page.
 */
export function useLeaderboard(filter: StatsFilter = LIFETIME): UseQueryResult<Leaderboard> {
  return useQuery({
    queryKey: leaderboardKey(filter),
    queryFn: () => unwrap(api.GET('/api/stats/leaderboard', { params: { query: query(filter) } })),
  })
}
