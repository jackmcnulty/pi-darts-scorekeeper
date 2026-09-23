/**
 * Reading and writing the player list.
 *
 * Every type here is an alias of a generated one -- nothing describes a payload
 * by hand -- and every write invalidates the whole `players` key rather than
 * patching the cache, because the server decides things the client did not ask
 * for: which colour a new player gets, and what a trimmed name looks like.
 * Re-reading is one round trip over a LAN and it cannot be wrong.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query'
import { api, unwrap } from './client'
import type { components } from './schema'

export type Player = components['schemas']['PlayerResponse']

/** The body of a create or an edit. Optional fields mean different things to each — see the schema. */
export type PlayerWrite = components['schemas']['PlayerWrite']

/** The root every player query hangs off, so one invalidation covers them all. */
export const PLAYERS_KEY = ['players'] as const

export function playersKey(includeArchived: boolean) {
  return [...PLAYERS_KEY, { includeArchived }] as const
}

/**
 * The player list, active-only unless asked otherwise.
 *
 * The default is the picker's list: `GET /api/players` excludes archived
 * players server-side, so hiding them is not something a screen can forget to
 * do. The management screen is the one caller that passes `true`.
 */
export function usePlayers(includeArchived = false): UseQueryResult<Player[]> {
  return useQuery({
    queryKey: playersKey(includeArchived),
    queryFn: () =>
      unwrap(api.GET('/api/players', { params: { query: { include_archived: includeArchived } } })),
  })
}

export function useCreatePlayer(): UseMutationResult<Player, Error, PlayerWrite> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: PlayerWrite) => unwrap(api.POST('/api/players', { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: PLAYERS_KEY }),
  })
}

export interface PlayerEdit {
  id: number
  body: PlayerWrite
}

export function useUpdatePlayer(): UseMutationResult<Player, Error, PlayerEdit> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: PlayerEdit) =>
      unwrap(api.PATCH('/api/players/{player_id}', { params: { path: { player_id: id } }, body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: PLAYERS_KEY }),
  })
}

export function useArchivePlayer(): UseMutationResult<Player, Error, number> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.POST('/api/players/{player_id}/archive', { params: { path: { player_id: id } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: PLAYERS_KEY }),
  })
}
