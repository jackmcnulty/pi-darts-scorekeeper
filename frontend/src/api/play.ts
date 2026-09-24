/**
 * The play screen's three calls, and the one rule that shapes all of them.
 *
 * Every write returns the whole new `MatchStateResponse`, which `darts.api.play`
 * does deliberately: a second round trip the client has to know to make is a
 * race, and a scoreboard repainted from two responses can show a score from one
 * and a thrower from the other. So a success here writes the response straight
 * into the cache with `setQueryData` rather than invalidating -- the answer is
 * already in hand, and refetching it would be both slower and a chance to paint
 * something newer than the dart just thrown.
 *
 * Nothing is patched locally. There is no optimistic update, no client-side
 * bust revert and no recomputed checkout: all three are in the payload, and the
 * server is the only thing that knows the rules. `#24` is a rendering problem.
 *
 * Neither mutation retries, which is the house rule from #21 and load-bearing
 * here. A dart whose request timed out may well have been recorded, so an
 * automatic retry could double it. `client_dart_id` is what makes the *manual*
 * retry safe instead: the same id replayed inserts nothing and returns the
 * state as it stands, so `Play.tsx` keeps the id of a failed dart and re-sends
 * it unchanged.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query'
import { api, unwrap } from './client'
import { MATCHES_KEY } from './matches'
import type { components } from './schema'

export type MatchState = components['schemas']['MatchStateResponse']
export type LegState = components['schemas']['LegStateResponse']
export type TeamLeg = components['schemas']['TeamLegResponse']
export type Visit = components['schemas']['VisitResponse']
export type Checkout = components['schemas']['CheckoutResponse']
export type Thrower = components['schemas']['ThrowerResponse']
export type DartWrite = components['schemas']['DartWrite']

/** Under `MATCHES_KEY`, so #23's create-match invalidation reaches it too. */
export function matchStateKey(matchId: number) {
  return [...MATCHES_KEY, matchId, 'state'] as const
}

export function useMatchState(matchId: number): UseQueryResult<MatchState> {
  return useQuery({
    queryKey: matchStateKey(matchId),
    queryFn: () =>
      unwrap(api.GET('/api/matches/{match_id}/state', { params: { path: { match_id: matchId } } })),
    // Zero, which `queryClient.ts` predicted this screen would want. Every
    // write already replaces the cache with the authoritative answer, so the
    // only thing staleness buys here is a window in which a second phone at the
    // same board shows a score that has moved on.
    staleTime: 0,
  })
}

export interface DartInput {
  legId: number
  body: DartWrite
}

export function useRecordDart(matchId: number): UseMutationResult<MatchState, Error, DartInput> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ legId, body }: DartInput) =>
      unwrap(api.POST('/api/legs/{leg_id}/darts', { params: { path: { leg_id: legId } }, body })),
    onSuccess: (state) => queryClient.setQueryData(matchStateKey(matchId), state),
  })
}

export function useUndoDart(matchId: number): UseMutationResult<MatchState, Error, number> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (legId: number) =>
      unwrap(api.POST('/api/legs/{leg_id}/undo', { params: { path: { leg_id: legId } } })),
    onSuccess: (state) => queryClient.setQueryData(matchStateKey(matchId), state),
  })
}
