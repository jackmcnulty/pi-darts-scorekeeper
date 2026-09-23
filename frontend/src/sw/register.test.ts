/**
 * Registration, in the three situations it has to survive.
 *
 * Vitest runs as a dev build, so `import.meta.env.DEV` is true by default
 * here and every production-path test has to stub it. That is the point of
 * reading it inside the function rather than at module scope.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { registerServiceWorker } from './register'

function withServiceWorker(register: () => Promise<unknown>) {
  const api = { register: vi.fn(register) }
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: api,
  })
  return api
}

afterEach(() => {
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
  Reflect.deleteProperty(navigator, 'serviceWorker')
})

describe('in development', () => {
  it('does not register, because /sw.js only exists in a build', () => {
    // A worker installed during a dev session serves a cached shell over the
    // top of the dev server, which looks exactly like HMR being broken.
    const api = withServiceWorker(() => Promise.resolve({}))
    vi.stubEnv('DEV', true)

    registerServiceWorker()

    expect(api.register).not.toHaveBeenCalled()
  })
})

describe('in a build', () => {
  it('registers /sw.js at the root scope', () => {
    const api = withServiceWorker(() => Promise.resolve({}))
    vi.stubEnv('DEV', false)

    registerServiceWorker()

    expect(api.register).toHaveBeenCalledWith('/sw.js', { type: 'module', scope: '/' })
  })

  it('says so and carries on when registration is refused', async () => {
    // A worker that will not install is an app that needs the network to
    // start. That is worth a line in the console; it is not worth a white
    // screen, so nothing here is allowed to throw.
    const error = new Error('SecurityError')
    const api = withServiceWorker(() => Promise.reject(error))
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubEnv('DEV', false)

    expect(() => {
      registerServiceWorker()
    }).not.toThrow()

    await vi.waitFor(() => {
      expect(logged).toHaveBeenCalledWith('service worker registration failed', error)
    })
    expect(api.register).toHaveBeenCalledOnce()
  })
})

describe('on a browser without service workers', () => {
  it('does nothing and does not throw', () => {
    vi.stubEnv('DEV', false)
    expect('serviceWorker' in navigator).toBe(false)

    expect(() => {
      registerServiceWorker()
    }).not.toThrow()
  })
})
