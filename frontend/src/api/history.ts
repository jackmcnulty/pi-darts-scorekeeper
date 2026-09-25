/**
 * Reading matches that have already been played: the list, and one match's darts.
 *
 * Three calls, all reads, all cached under `MATCHES_KEY` so that #23's
 * create-match invalidation reaches them -- a new match belongs at the top of
 * the history list, and the list is wrong until it refetches.
 *
 * Paginated on the server, not here
 * ---------------------------------
 * `GET /api/matches` has taken `limit` and `offset` since #17 and returns
 * `total` alongside the page, so #26's "does not fetch the full history at
 * once" is a matter of asking for one page and trusting the count. Nothing
 * accumulates pages in the client: the screen shows the page it is on, which is
 * what keeps the Pi from serialising four hundred matches to answer a question
 * about twenty.
 *
 * `placeholderData` holds the previous page on screen while the next one is in
 * flight, so paging does not blank the list and jump the scroll position. It is
 * the one thing here that is about feel rather than data.
 *
 * Why the darts are a separate call
 * ---------------------------------
 * `GET /api/matches/{id}/darts` is #26's own endpoint and exists because
 * `/state` structurally cannot answer this: it carries two visits of the
 * current leg and both move on as the leg does. The match detail screen needs
 * every visit of every leg, which is a different question and so a different
 * request. It is not paginated -- one match's darts are bounded by `best_of`.
 */
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import type { UseQueryResult } from '@tanstack/react-query'
import { api, unwrap } from './client'
import { MATCHES_KEY } from './matches'
import type { components } from './schema'

export type MatchPage = components['schemas']['MatchPage']
export type MatchHistory = components['schemas']['MatchHistoryResponse']
export type LegHistory = components['schemas']['LegHistoryResponse']
export type MatchStats = components['schemas']['MatchReportResponse']
export type LegLine = components['schemas']['LegLineResponse']
export type MatchStatus = components['schemas']['MatchStatus']

/** How many matches a page of history holds. */
export const PAGE_SIZE = 20

/**
 * Keys under `MATCHES_KEY`, but behind a `'history'` segment of their own.
 *
 * Deliberately not `[...MATCHES_KEY, { status }]`: that is `RESUMABLE_KEY`'s
 * exact shape, and a history query that happened to ask for `in_progress` would
 * otherwise share a cache entry with the resume card -- which asks for
 * `limit: 1` and would hand the list one match. The extra segment makes the
 * collision impossible rather than unlikely.
 */
export function historyKey(status: MatchStatus | null, offset: number) {
  return [...MATCHES_KEY, 'history', { status, offset }] as const
}

export function matchDartsKey(matchId: number) {
  return [...MATCHES_KEY, matchId, 'darts'] as const
}

export function matchStatsKey(matchId: number) {
  return [...MATCHES_KEY, matchId, 'stats'] as const
}

/** One page of matches, newest first, filtered by status when one is given. */
export function useMatchHistory(
  status: MatchStatus | null,
  offset: number,
): UseQueryResult<MatchPage> {
  return useQuery({
    queryKey: historyKey(status, offset),
    queryFn: () =>
      unwrap(
        api.GET('/api/matches', {
          params: {
            query: {
              limit: PAGE_SIZE,
              offset,
              // Omitted rather than sent as null: `?status=` is optional on the
              // route and "every status" is its absence, not a value.
              ...(status === null ? {} : { status }),
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  })
}

/** Every dart of one match, grouped by leg and visit. #26's own endpoint. */
export function useMatchDarts(matchId: number): UseQueryResult<MatchHistory> {
  return useQuery({
    queryKey: matchDartsKey(matchId),
    queryFn: () =>
      unwrap(api.GET('/api/matches/{match_id}/darts', { params: { path: { match_id: matchId } } })),
  })
}

/**
 * One match's statistics, which is where the per-player numbers come from.
 *
 * `LegLineResponse` is per player per leg, so it answers both sheets: the leg
 * sheet's "per-player 3-dart averages for the leg" and, summed by the report's
 * own `players` block, the match sheet's per-player totals. `TeamLegResponse`
 * on `/state` carries an average too, but per *team* -- #26 asks for per
 * player, so this is the right source, and unlike the play payload it survives
 * a refresh.
 */
export function useMatchStats(matchId: number): UseQueryResult<MatchStats> {
  return useQuery({
    queryKey: matchStatsKey(matchId),
    queryFn: () =>
      unwrap(api.GET('/api/stats/matches/{match_id}', { params: { path: { match_id: matchId } } })),
  })
}
