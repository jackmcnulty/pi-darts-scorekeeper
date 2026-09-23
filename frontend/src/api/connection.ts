/**
 * Whether the phone can currently see the Pi, decided by evidence.
 *
 * `navigator.onLine` is not that evidence. It reports an association with an
 * access point, which in a garage is routinely true while the Pi is off, on
 * another subnet, or still booting -- exactly the cases this has to catch.
 * So nothing here asks the browser. The state moves only when a real request
 * really succeeds or really fails:
 *
 * * an `OfflineError` -- the request got no answer at all -- loses the
 *   connection;
 * * *any* answer finds it again, including a 404 or a 409. A server that
 *   refuses is a server that is plainly there, and treating only 2xx as proof
 *   would leave the toast up through a perfectly healthy argument about a
 *   duplicate name.
 *
 * Recovery therefore needs a request to have been made. TanStack Query's
 * retries, `refetchOnReconnect` and `refetchOnWindowFocus` are what make one
 * happen -- see `queryClient.ts`. There is no polling and no timer here, so
 * there is nothing in this module whose correctness depends on a clock.
 */
import { useSyncExternalStore } from 'react'
import { OfflineError } from './client'

type Listener = () => void

export class ConnectionMonitor {
  #reachable = true
  #listeners = new Set<Listener>()

  /** Called with whatever a request threw. */
  recordFailure(error: unknown): void {
    this.#set(!(error instanceof OfflineError))
  }

  /** Called when a request came back with anything at all. */
  recordSuccess(): void {
    this.#set(true)
  }

  get reachable(): boolean {
    return this.#reachable
  }

  subscribe = (listener: Listener): (() => void) => {
    this.#listeners.add(listener)
    return () => this.#listeners.delete(listener)
  }

  getSnapshot = (): boolean => this.#reachable

  #set(reachable: boolean): void {
    // Only a change notifies. Every dart thrown is a mutation and a refetch,
    // and re-rendering the whole shell on each one to say "still fine" would
    // be a re-render per throw for no visible difference.
    if (reachable === this.#reachable) return
    this.#reachable = reachable
    for (const listener of this.#listeners) listener()
  }
}

/** The app's own monitor. Tests build their own rather than reset this one. */
export const connection = new ConnectionMonitor()

/** Re-renders the caller whenever reachability changes, and only then. */
export function useReachable(monitor: ConnectionMonitor = connection): boolean {
  return useSyncExternalStore(monitor.subscribe, monitor.getSnapshot, monitor.getSnapshot)
}
