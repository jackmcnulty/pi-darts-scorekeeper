/**
 * The one match the home screen cares about: the one still being played.
 *
 * There is at most one, because #23 will not start a match while another is in
 * progress, and `GET /api/matches?status=in_progress&limit=1` answers it in a
 * single request -- `MatchResponse` already carries the teams and their members,
 * so the resume card needs nothing else to name who is playing.
 */
import { useQuery } from '@tanstack/react-query'
import type { UseQueryResult } from '@tanstack/react-query'
import { api, unwrap } from './client'
import type { components } from './schema'

export type Match = components['schemas']['MatchResponse']

export const RESUMABLE_KEY = ['matches', { status: 'in_progress' }] as const

/** The match to offer resuming, or `null` when there is nothing to resume. */
export function useResumableMatch(): UseQueryResult<Match | null> {
  return useQuery({
    queryKey: RESUMABLE_KEY,
    queryFn: async () => {
      const page = await unwrap(
        api.GET('/api/matches', { params: { query: { status: 'in_progress', limit: 1 } } }),
      )
      // `?? null` rather than `?? undefined`: TanStack Query treats an
      // `undefined` result as a query that returned nothing and warns, and
      // "there is no match" is a real answer rather than a missing one.
      return page.items[0] ?? null
    },
  })
}
