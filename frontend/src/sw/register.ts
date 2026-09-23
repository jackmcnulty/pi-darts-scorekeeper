/**
 * Installing the worker, and declining to in the two cases where it hurts.
 *
 * Registration is deliberately not awaited and never throws upward. A worker
 * that fails to install is a phone that has to be online to start the app --
 * annoying, and exactly the situation before #21 -- but a *thrown* failure
 * during boot is a white screen, which is worse than the problem it reports.
 */

export function registerServiceWorker(): void {
  if (!('serviceWorker' in navigator)) return

  // Read here rather than at module scope so it is a decision the function
  // makes, and therefore one a test can drive both ways. Vite replaces it
  // with a literal at build time either way, so the branch still disappears
  // from the bundle.
  //
  // Vite serves modules unbundled in development, so there is no `/sw.js` to
  // register -- it exists only in `dist`. Worse, a worker registered once
  // during a dev session goes on serving a cached shell over the top of the
  // dev server, which looks exactly like HMR being broken.
  if (import.meta.env.DEV) return

  // `type: 'module'` because the worker is a Rollup ES entry. Safari has
  // supported module workers since 16.4 and this app targets a current
  // iPhone; src/pwa.test.ts asserts the emitted file is self-contained, so
  // there is never a bare import for an older engine to trip over.
  navigator.serviceWorker
    .register('/sw.js', { type: 'module', scope: '/' })
    .catch((error: unknown) => {
      // Nothing else to do about it. Said out loud so it is findable in Safari's
      // console rather than being a PWA that silently never installs.
      console.error('service worker registration failed', error)
    })
}
