/**
 * What the service worker does with a request, as plain functions.
 *
 * Everything that makes a decision lives here rather than in `sw.ts`, because
 * #21 requires proof -- "the service worker **never** serves a cached `/api`
 * response, verified by a test, not by inspection" -- and a decision buried in
 * an event listener inside a worker global can only be inspected. `sw.ts` is
 * the twelve lines that cannot be tested; this is everything that can.
 *
 * The offline story is deliberately small. The app shell loads without a
 * network; the game does not run without one. Every score, every checkout and
 * every undo is the server's to decide, and a cached `/api` response is not a
 * stale view of the match -- it is a *different* match, one where the phone
 * and the board disagree about who is winning. That is the reason rule one
 * below exists and why it is first.
 */

/** Bumping this name is what retires every previously cached file. */
export const CACHE_NAME = 'darts-shell-v1'

/** Cached at install so a cold, offline launch still has something to boot. */
export const PRECACHE = ['/', '/manifest.webmanifest', '/favicon.svg', '/icon-192.png']

/**
 * A Vite content hash: `/assets/index-BKd04slA.js`.
 *
 * The same rule `darts.api.static.HASHED` applies on the server, for the same
 * reason: a name that changes whenever the bytes do can be cached forever and
 * never revalidated. Kept in step with it by
 * `tests/api/test_static.py` on one side and `handler.test.ts` on the other.
 */
export const HASHED = /\/assets\/.+-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$/

export type Strategy =
  /** Straight to the network, never cached, never read from cache. */
  | 'network-only'
  /** Cache wins if present; otherwise fetch and keep it. */
  | 'cache-first'
  /** Network wins; the cached shell is the fallback so deep links still boot. */
  | 'shell-fallback'
  /** Network wins; whatever was cached is the fallback. */
  | 'network-first'

export interface Routable {
  url: string
  method: string
  mode: string
}

/**
 * The single decision this worker makes.
 *
 * Order matters. `/api` is checked before anything else so that no later rule
 * can ever reach it, including by accident: a future rule that decided to
 * cache "everything JSON" would otherwise quietly start serving yesterday's
 * leg state.
 */
export function chooseStrategy(request: Routable, origin: string): Strategy {
  // A POST is a dart being thrown. Nothing about it is cacheable, and the
  // Cache API cannot store it anyway.
  if (request.method !== 'GET') return 'network-only'

  const url = new URL(request.url, origin)

  // Another origin's problem. There are none in this app, and a service
  // worker quietly caching one would be a surprise.
  if (url.origin !== origin) return 'network-only'

  if (isApi(url)) return 'network-only'

  if (request.mode === 'navigate') return 'shell-fallback'

  if (HASHED.test(url.pathname)) return 'cache-first'

  return 'network-first'
}

export function isApi(url: URL): boolean {
  return url.pathname === '/api' || url.pathname.startsWith('/api/')
}

/** The bits of the Cache API this worker uses, so a test can supply them. */
export interface CacheLike {
  match(request: Request): Promise<Response | undefined>
  put(request: Request, response: Response): Promise<void>
}

export interface Dependencies {
  cache: CacheLike
  fetch: (request: Request) => Promise<Response>
  /** Used by `shell-fallback` when the network is gone. */
  shell: string
}

/**
 * Should the worker touch this request at all?
 *
 * Synchronous, because `respondWith` must be called synchronously or not at
 * all -- and "not at all" is the entire `/api` guarantee. A declined request
 * is made by the browser exactly as if no worker were installed: it never
 * enters `respond` below, so there is no code path, present or future, in
 * which a cached response could be substituted for a live one.
 *
 * An `async` decision could not do this. It would have to call `respondWith`
 * first and work out what to do afterwards, putting every `/api` call inside
 * the worker's control flow and making the guarantee a matter of reading the
 * code carefully rather than a matter of structure.
 */
export function handles(strategy: Strategy): boolean {
  return strategy !== 'network-only'
}

/**
 * Answer one request the worker has agreed to handle.
 *
 * `network-only` never arrives here -- `handles` is what stops it -- but it is
 * still given the one behaviour that cannot be wrong if it somehow did.
 */
export async function respond(
  request: Request,
  strategy: Strategy,
  origin: string,
  deps: Dependencies,
): Promise<Response> {
  if (strategy === 'network-only') return deps.fetch(request)

  if (strategy === 'cache-first') {
    const cached = await deps.cache.match(request)
    if (cached !== undefined) return cached
    return store(request, deps)
  }

  try {
    return await store(request, deps)
  } catch (error) {
    const fallback =
      strategy === 'shell-fallback'
        ? await deps.cache.match(new Request(new URL(deps.shell, origin)))
        : await deps.cache.match(request)
    if (fallback !== undefined) return fallback
    throw error
  }
}

/** Fetch, and keep a copy if it is worth keeping. */
async function store(request: Request, deps: Dependencies): Promise<Response> {
  const response = await deps.fetch(request)
  // An error page is not the app. Caching a 404 or a 503 would pin the
  // failure in place until the cache name changes.
  if (response.ok) await deps.cache.put(request, response.clone())
  return response
}
