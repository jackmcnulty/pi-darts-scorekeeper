/**
 * Cache and retry policy, chosen for one Pi on one LAN.
 *
 * The distances here are nothing like the public internet's. A round trip to
 * the Pi is a millisecond or two, so a retry is cheap and a backoff measured
 * in seconds is just a screen sitting blank for no reason. What *is* expensive
 * is being wrong about game state, because the server is authoritative for all
 * of it and the phone is only ever a view.
 */
import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query'
import { isRetryable } from './client'
import { connection, type ConnectionMonitor } from './connection'

/** Three tries then give up and say so. Over a LAN this is about a second. */
export const MAX_ATTEMPTS = 3

/** First retry after this, then doubling, capped. */
export const RETRY_BASE_MS = 150
export const RETRY_CAP_MS = 2_000

/**
 * How long a fetched answer is trusted without re-asking.
 *
 * Long enough that navigating between screens does not re-request everything,
 * short enough that a second phone watching the same match is never far
 * behind. Individual screens override this where they know better; #24's play
 * screen will want zero.
 */
export const STALE_TIME_MS = 5_000

export function retryDelay(attempt: number): number {
  return Math.min(RETRY_BASE_MS * 2 ** attempt, RETRY_CAP_MS)
}

export function shouldRetry(failureCount: number, error: unknown): boolean {
  return failureCount < MAX_ATTEMPTS && isRetryable(error)
}

export function createQueryClient(monitor: ConnectionMonitor = connection): QueryClient {
  // Both caches report to the monitor, because both are evidence. A mutation
  // is usually the first thing to notice a dropped connection -- it happens
  // the instant a dart is entered, where a query might not run for minutes.
  const onError = (error: unknown) => {
    monitor.recordFailure(error)
  }
  const onSuccess = () => {
    monitor.recordSuccess()
  }

  return new QueryClient({
    queryCache: new QueryCache({ onError, onSuccess }),
    mutationCache: new MutationCache({ onError, onSuccess }),
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        retryDelay,
        staleTime: STALE_TIME_MS,
        // The phone locks mid-leg and comes back minutes later, by which time
        // somebody else may have thrown. Refetching on focus is what stops it
        // showing a score that is quietly out of date.
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
      },
      mutations: {
        // Never. `POST /api/legs/{leg_id}/darts` records a dart; it is not
        // idempotent, and a retry after a response that was sent but never
        // arrived would score the same dart twice. A failed mutation is left
        // failed, the toast explains why, and the player taps again -- which
        // is a decision they can see, rather than one made for them.
        retry: false,
      },
    },
  })
}
