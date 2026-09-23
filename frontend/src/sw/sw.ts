/// <reference lib="webworker" />
/**
 * The worker itself: listeners, and nothing else.
 *
 * Every decision is in `handler.ts`, which is why this file has no branches
 * worth testing and `handler.test.ts` has all of them. Keeping it this thin
 * is the point -- it is the only part of the PWA layer that runs in a context
 * Vitest cannot enter.
 *
 * `lib: webworker` comes from `tsconfig.sw.json`. It cannot come from
 * `tsconfig.app.json`, whose `DOM` lib declares an incompatible `self`.
 */
import { CACHE_NAME, chooseStrategy, handles, PRECACHE, respond } from './handler'

declare const self: ServiceWorkerGlobalScope

self.addEventListener('install', (event) => {
  // Skip waiting: the Pi is power-cycled constantly and there is exactly one
  // user per phone. Waiting for every tab to close before an update applies
  // would mean a stale shell surviving reboots for no benefit.
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))),
      )
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const origin = self.location.origin
  const strategy = chooseStrategy(event.request, origin)

  // Every `/api` call leaves through here, before `respondWith` is reached.
  // The browser then makes the request itself, untouched by this worker.
  if (!handles(strategy)) return

  event.respondWith(
    respond(event.request, strategy, origin, {
      cache: {
        match: (request) => caches.open(CACHE_NAME).then((cache) => cache.match(request)),
        put: (request, response) =>
          caches.open(CACHE_NAME).then((cache) => cache.put(request, response)),
      },
      fetch: (request) => fetch(request),
      shell: '/',
    }),
  )
})
