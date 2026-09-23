/**
 * Starting a match, and finding the one that is still being played.
 *
 * `GET /api/matches?status=in_progress&limit=1` answers the second in a single
 * request -- `MatchResponse` already carries the teams and their members, so
 * the resume card needs nothing else to name who is playing.
 *
 * There can be more than one. #22 said here that there could not, on the
 * grounds that #23 would refuse to start a match while another was in
 * progress; that was a prediction about an unwritten screen, and it is not
 * what either side does. The server has no such guard -- two `POST
 * /api/matches` in a row both return 201 -- and #23 warns rather than refuses,
 * because on a shared phone at a board "start another one" is a thing people
 * legitimately do. So this is the *newest* in-progress match, which is what
 * `list_matches` means by `ORDER BY created_at DESC, id DESC`, and an older
 * one is not lost: it stays in progress and #26's history screen will show it.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query'
import { api, unwrap } from './client'
import type { components } from './schema'

export type Match = components['schemas']['MatchResponse']

/** The body of a match setup. Generated, not written -- see `setup/config.ts`. */
export type MatchWrite = components['schemas']['MatchWrite']

/** The root every match query hangs off, so one invalidation covers them all. */
export const MATCHES_KEY = ['matches'] as const

export const RESUMABLE_KEY = [...MATCHES_KEY, { status: 'in_progress' }] as const

/** The newest match to offer resuming, or `null` when there is nothing to resume. */
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

/**
 * Start a match. Resolves to the created match, whose `id` is where to go next.
 *
 * Invalidates every match query rather than seeding the cache with the
 * response: the new match is now the resumable one, and the server decided
 * things the payload did not say -- the team ids, the first leg, who throws.
 * Like every mutation in this app it does not retry, because a create that
 * timed out may well have succeeded.
 */
export function useCreateMatch(): UseMutationResult<Match, Error, MatchWrite> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: MatchWrite) => unwrap(api.POST('/api/matches', { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: MATCHES_KEY }),
  })
}
