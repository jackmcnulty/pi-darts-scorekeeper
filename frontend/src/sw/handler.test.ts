/**
 * The service worker's routing decision, and the one rule that must not bend.
 *
 * #21: "The service worker **never** serves a cached `/api` response --
 * verified by a test, not by inspection." That is `the /api rule` below. It
 * is checked twice over, from both ends: that the worker declines every `/api`
 * request before `respondWith` is reachable, and that a cache stuffed with
 * `/api` answers is never consulted even if one got that far.
 */
import { describe, expect, it, vi } from 'vitest'
import {
  CACHE_NAME,
  chooseStrategy,
  handles,
  HASHED,
  isApi,
  PRECACHE,
  respond,
  type CacheLike,
  type Dependencies,
} from './handler'

const ORIGIN = 'http://darts.local'

function request(url: string, init: { method?: string; mode?: string } = {}) {
  return { url, method: init.method ?? 'GET', mode: init.mode ?? 'cors' }
}

/** A cache that fails the test if anything touches it. */
function forbiddenCache(): CacheLike {
  return {
    match: () => {
      throw new Error('the cache was read for a request that must never be cached')
    },
    put: () => {
      throw new Error('the cache was written for a request that must never be cached')
    },
  }
}

function recordingCache(entries: Record<string, Response> = {}) {
  const store = new Map(Object.entries(entries))
  const put = vi.fn((req: Request, response: Response) => {
    store.set(req.url, response)
    return Promise.resolve()
  })
  const match = vi.fn((req: Request) => Promise.resolve(store.get(req.url)))
  return { cache: { match, put } satisfies CacheLike, store, put, match }
}

function deps(cache: CacheLike, fetcher: Dependencies['fetch']): Dependencies {
  return { cache, fetch: fetcher, shell: '/' }
}

// --- the /api rule ---------------------------------------------------------

describe('the /api rule', () => {
  const apiPaths = [
    '/api/healthz',
    '/api/players',
    '/api/matches/7/state',
    '/api/legs/3/darts',
    '/api/legs/3/checkout',
    '/api/stats/leaderboard',
    '/api/export/matches.csv',
    '/api/openapi.json',
    '/api',
  ]

  it.each(apiPaths)('%s is network-only', (path) => {
    expect(chooseStrategy(request(`${ORIGIN}${path}`), ORIGIN)).toBe('network-only')
  })

  it.each(apiPaths)('%s is declined outright, so respondWith is never reached', (path) => {
    // This is the guarantee. `handles` is called synchronously in the fetch
    // listener, and a false here means the browser makes the request itself.
    expect(handles(chooseStrategy(request(`${ORIGIN}${path}`), ORIGIN))).toBe(false)
  })

  it('stays network-only even for a navigation that looks like an api path', () => {
    const navigation = request(`${ORIGIN}/api/players`, { mode: 'navigate' })
    expect(chooseStrategy(navigation, ORIGIN)).toBe('network-only')
  })

  it('stays network-only even if an api path is dressed as a hashed asset', () => {
    // A defence against the rule order ever being rearranged: this URL
    // matches HASHED, and must still lose to the /api check.
    const url = `${ORIGIN}/api/assets/index-BKd04slA.js`
    expect(HASHED.test('/api/assets/index-BKd04slA.js')).toBe(true)
    expect(chooseStrategy(request(url), ORIGIN)).toBe('network-only')
  })

  it('never reads the cache for an api request, even if asked to', async () => {
    // The belt to the braces above: if some future edit did route an /api
    // request into respond(), it still could not be answered from the cache.
    const fetcher = vi.fn(() => Promise.resolve(new Response('live')))
    const response = await respond(
      new Request(`${ORIGIN}/api/players`),
      'network-only',
      ORIGIN,
      deps(forbiddenCache(), fetcher),
    )

    expect(await response.text()).toBe('live')
    expect(fetcher).toHaveBeenCalledOnce()
  })

  it('does not mistake a path that merely starts with the letters api', () => {
    expect(isApi(new URL(`${ORIGIN}/apiary`))).toBe(false)
    expect(chooseStrategy(request(`${ORIGIN}/apiary`), ORIGIN)).toBe('network-first')
  })
})

// --- everything else -------------------------------------------------------

describe('choosing a strategy', () => {
  it('sends a hashed asset to the cache first', () => {
    const url = `${ORIGIN}/assets/index-BKd04slA.js`
    expect(chooseStrategy(request(url), ORIGIN)).toBe('cache-first')
  })

  it('sends a navigation to the network with the shell behind it', () => {
    const deep = request(`${ORIGIN}/history/42`, { mode: 'navigate' })
    expect(chooseStrategy(deep, ORIGIN)).toBe('shell-fallback')
  })

  it('sends the manifest and icons to the network first', () => {
    // Unhashed, so the server sends no-cache and a new one must be noticed.
    expect(chooseStrategy(request(`${ORIGIN}/manifest.webmanifest`), ORIGIN)).toBe('network-first')
    expect(chooseStrategy(request(`${ORIGIN}/icon-192.png`), ORIGIN)).toBe('network-first')
  })

  it.each(['POST', 'PATCH', 'DELETE'])('never caches a %s', (method) => {
    const url = `${ORIGIN}/assets/index-BKd04slA.js`
    expect(chooseStrategy(request(url, { method }), ORIGIN)).toBe('network-only')
  })

  it('leaves another origin alone', () => {
    expect(chooseStrategy(request('https://example.com/x.js'), ORIGIN)).toBe('network-only')
  })
})

describe('cache-first', () => {
  it('serves the cached copy without asking the network', async () => {
    const url = `${ORIGIN}/assets/index-BKd04slA.js`
    const { cache } = recordingCache({ [url]: new Response('cached') })
    const fetcher = vi.fn(() => Promise.resolve(new Response('live')))

    const response = await respond(new Request(url), 'cache-first', ORIGIN, deps(cache, fetcher))

    expect(await response.text()).toBe('cached')
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('fetches and keeps a copy on the first miss', async () => {
    const url = `${ORIGIN}/assets/index-BKd04slA.js`
    const { cache, put } = recordingCache()
    const fetcher = vi.fn(() => Promise.resolve(new Response('live')))

    const response = await respond(new Request(url), 'cache-first', ORIGIN, deps(cache, fetcher))

    expect(await response.text()).toBe('live')
    expect(put).toHaveBeenCalledOnce()
  })
})

describe('shell-fallback', () => {
  it('prefers the network while there is one', async () => {
    const { cache } = recordingCache({ [`${ORIGIN}/`]: new Response('stale shell') })
    const fetcher = vi.fn(() => Promise.resolve(new Response('fresh')))

    const response = await respond(
      new Request(`${ORIGIN}/history/42`),
      'shell-fallback',
      ORIGIN,
      deps(cache, fetcher),
    )

    expect(await response.text()).toBe('fresh')
  })

  it('falls back to the cached shell for a deep link with no network', async () => {
    // The offline promise, exactly: the app shell loads and React routes to
    // /history/42 itself. It will have nothing to show until the Pi answers,
    // which is the point -- the server owns the match, not the phone.
    const { cache } = recordingCache({ [`${ORIGIN}/`]: new Response('shell') })
    const fetcher = vi.fn(() => Promise.reject(new TypeError('Failed to fetch')))

    const response = await respond(
      new Request(`${ORIGIN}/history/42`),
      'shell-fallback',
      ORIGIN,
      deps(cache, fetcher),
    )

    expect(await response.text()).toBe('shell')
  })

  it('rethrows when there is no network and no cached shell', async () => {
    const { cache } = recordingCache()
    const fetcher = vi.fn(() => Promise.reject(new TypeError('Failed to fetch')))

    await expect(
      respond(new Request(`${ORIGIN}/history/42`), 'shell-fallback', ORIGIN, deps(cache, fetcher)),
    ).rejects.toThrow('Failed to fetch')
  })
})

describe('network-first', () => {
  it('falls back to whatever was cached for that exact request', async () => {
    const url = `${ORIGIN}/icon-192.png`
    const { cache } = recordingCache({ [url]: new Response('cached icon') })
    const fetcher = vi.fn(() => Promise.reject(new TypeError('Failed to fetch')))

    const response = await respond(new Request(url), 'network-first', ORIGIN, deps(cache, fetcher))

    expect(await response.text()).toBe('cached icon')
  })
})

describe('what gets kept', () => {
  it('never caches a failed response', async () => {
    // Caching a 503 would pin the failure in place until the cache name moves.
    const url = `${ORIGIN}/icon-192.png`
    const { cache, put } = recordingCache()
    const fetcher = vi.fn(() => Promise.resolve(new Response('nope', { status: 503 })))

    await respond(new Request(url), 'network-first', ORIGIN, deps(cache, fetcher))

    expect(put).not.toHaveBeenCalled()
  })
})

describe('the precache list', () => {
  it('holds the shell, so a cold offline launch has something to boot', () => {
    expect(PRECACHE).toContain('/')
  })

  it('is all same-origin absolute paths, which is what cache.addAll needs', () => {
    for (const entry of PRECACHE) expect(entry.startsWith('/')).toBe(true)
  })

  it('never precaches anything under /api', () => {
    for (const entry of PRECACHE) expect(isApi(new URL(entry, ORIGIN))).toBe(false)
  })

  it('is versioned, so bumping the name retires the old files', () => {
    expect(CACHE_NAME).toMatch(/-v\d+$/)
  })
})
