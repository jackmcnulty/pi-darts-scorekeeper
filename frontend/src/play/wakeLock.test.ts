/**
 * The wake lock, including the two paths that only happen on a real phone.
 *
 * `Play.test.tsx` covers the policy -- held while a match is in progress,
 * released when it ends. What is left is the lifecycle the browser imposes: it
 * takes the lock away whenever the page stops being visible and never gives it
 * back, so the re-acquire is the difference between a screen that stays awake
 * for a match and one that stays awake until somebody glances at another app.
 * That failure looks exactly like the feature was never wired up, and it is
 * invisible to a test that only checks `request` was called once.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useWakeLock } from './wakeLock'

/** A `WakeLockSentinel` as far as this hook is concerned. */
function fakeSentinel() {
  const listeners: (() => void)[] = []
  return {
    release: vi.fn(() => Promise.resolve()),
    addEventListener: vi.fn((_event: string, handler: () => void) => listeners.push(handler)),
    /** What the browser does when the page is hidden. */
    emitRelease: () => {
      for (const handler of listeners) handler()
    },
  }
}

function install(request: unknown) {
  // jsdom does not implement `wakeLock`, though `lib.dom.d.ts` types it as
  // non-optional -- so it is defined here rather than spied on.
  Object.defineProperty(navigator, 'wakeLock', { value: { request }, configurable: true })
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

afterEach(() => {
  Reflect.deleteProperty(navigator, 'wakeLock')
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
})

describe('while a match is being played', () => {
  it('asks for the screen once', async () => {
    const request = vi.fn(() => Promise.resolve(fakeSentinel()))
    install(request)

    renderHook(() => {
      useWakeLock(true)
    })

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith('screen')
    })
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('does not ask at all when the match is over', () => {
    const request = vi.fn(() => Promise.resolve(fakeSentinel()))
    install(request)

    renderHook(() => {
      useWakeLock(false)
    })

    expect(request).not.toHaveBeenCalled()
  })
})

describe('when the page goes away and comes back', () => {
  it('takes the lock again, because the browser does not give it back', async () => {
    const first = fakeSentinel()
    const second = fakeSentinel()
    const request = vi.fn(() => Promise.resolve(first))
    install(request)

    renderHook(() => {
      useWakeLock(true)
    })
    await waitFor(() => {
      expect(request).toHaveBeenCalledTimes(1)
    })

    // The phone locked: the browser released the lock and said so.
    request.mockImplementation(() => Promise.resolve(second))
    act(() => {
      first.emitRelease()
    })
    act(() => {
      setVisibility('hidden')
    })
    expect(request).toHaveBeenCalledTimes(1)

    // Somebody picked the phone back up mid-leg.
    act(() => {
      setVisibility('visible')
    })

    await waitFor(() => {
      expect(request).toHaveBeenCalledTimes(2)
    })
  })

  it('does not stack a second lock on top of one it still holds', async () => {
    const sentinel = fakeSentinel()
    const request = vi.fn(() => Promise.resolve(sentinel))
    install(request)

    renderHook(() => {
      useWakeLock(true)
    })
    await waitFor(() => {
      expect(request).toHaveBeenCalledTimes(1)
    })

    // Visible again without the browser ever having released it -- a focus
    // event, say. Asking for a second lock would leak the first.
    act(() => {
      setVisibility('visible')
    })

    await waitFor(() => {
      expect(request).toHaveBeenCalledTimes(1)
    })
    expect(sentinel.release).not.toHaveBeenCalled()
  })
})

describe('the awkward edges', () => {
  it('releases a lock that arrives after the screen has gone', async () => {
    // The request is in the air when the player leaves the match. Without this
    // the lock is granted to nobody and held until the tab closes.
    const sentinel = fakeSentinel()
    let grant: (value: typeof sentinel) => void = () => undefined
    install(
      vi.fn(
        () =>
          new Promise<typeof sentinel>((resolve) => {
            grant = resolve
          }),
      ),
    )

    const { unmount } = renderHook(() => {
      useWakeLock(true)
    })
    unmount()

    await act(async () => {
      grant(sentinel)
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(sentinel.release).toHaveBeenCalled()
    })
  })

  it('carries on when the browser refuses', async () => {
    // No user gesture yet, a battery saver, or -- the one that matters here --
    // an insecure context, since the Pi serves this app over plain HTTP.
    const request = vi.fn(() => Promise.reject(new Error('NotAllowedError')))
    install(request)

    const { unmount } = renderHook(() => {
      useWakeLock(true)
    })

    await waitFor(() => {
      expect(request).toHaveBeenCalled()
    })
    // A scoreboard that works and dims beats one that does not work.
    expect(() => {
      unmount()
    }).not.toThrow()
  })

  it('does nothing at all where the API is absent', () => {
    // jsdom, older iOS, and any plain-HTTP origin. `lib.dom.d.ts` types
    // `navigator.wakeLock` as always present, so TypeScript is no help here and
    // the feature test is what stops this throwing on every render.
    expect(Object.hasOwn(navigator, 'wakeLock')).toBe(false)

    expect(() => {
      renderHook(() => {
        useWakeLock(true)
      })
    }).not.toThrow()
  })
})
