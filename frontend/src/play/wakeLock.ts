/**
 * Holding the screen awake for as long as a match is being played.
 *
 * A board sits several seconds between visits and a phone that dims mid-leg has
 * to be woken and re-unlocked by somebody holding three darts, so #24 asks for
 * `navigator.wakeLock` for the duration of the match. `active` is the whole
 * policy: pass it whether the match is still being played, and the lock follows.
 *
 * Absent more often than you would think
 * --------------------------------------
 * `navigator.wakeLock` is typed as non-optional in `lib.dom.d.ts`, so
 * TypeScript will not warn about a browser that does not have it -- and jsdom
 * does not, which means an unguarded `navigator.wakeLock.request` throws in
 * every test that renders this screen. It is also genuinely missing on older
 * iOS and, more to the point here, outside a secure context: the Pi serves this
 * app over plain HTTP on the LAN. So the feature test is load-bearing rather
 * than defensive. The config uses `recommendedTypeChecked` rather than
 * `strictTypeChecked`, so `no-unnecessary-condition` is off and the guard lints
 * clean.
 *
 * Re-acquired on return, because a lock is lost on the way out
 * -----------------------------------------------------------
 * The browser releases the lock whenever the page stops being visible, and
 * never gives it back on its own. Without the `visibilitychange` handler the
 * screen would stay awake until the first time anybody glanced at another app
 * and never again for the rest of the match, which is the failure that looks
 * like the feature was never wired up.
 */
import { useEffect } from 'react'

export function useWakeLock(active: boolean): void {
  useEffect(() => {
    if (!active) return
    if (!('wakeLock' in navigator)) return

    let sentinel: WakeLockSentinel | null = null
    // The effect can be torn down while a request is still in the air, in which
    // case the lock arrives with nobody left to hold it.
    let cancelled = false

    const acquire = async () => {
      if (cancelled || sentinel !== null) return
      try {
        const lock = await navigator.wakeLock.request('screen')
        if (cancelled) {
          void lock.release()
          return
        }
        // The browser drops the lock when the page is hidden; hearing about it
        // is what lets `acquire` know there is one to take again.
        lock.addEventListener('release', () => {
          if (sentinel === lock) sentinel = null
        })
        sentinel = lock
      } catch {
        // Refused -- no user gesture yet, a battery-saver, or an insecure
        // context. A scoreboard that works and dims beats one that does not.
      }
    }

    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') void acquire()
    }

    void acquire()
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      cancelled = true
      document.removeEventListener('visibilitychange', onVisibilityChange)
      const held = sentinel
      sentinel = null
      if (held !== null) void held.release()
    }
  }, [active])
}
