/**
 * The retry policy, exercised against a server that really fails.
 *
 * Request counts are the hard assertions throughout. Nothing here asserts on
 * elapsed time: the policy's delays are real, so a timing assertion would be
 * measuring the CI runner rather than the decision, and #19 settled that the
 * deterministic property is what gets pinned.
 */
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { ApiError, api, OfflineError, unwrap, type ErrorEnvelope } from './client'
import { ConnectionMonitor } from './connection'
import {
  createQueryClient,
  MAX_ATTEMPTS,
  RETRY_BASE_MS,
  RETRY_CAP_MS,
  retryDelay,
  shouldRetry,
  STALE_TIME_MS,
} from './queryClient'

const server = setupServer()

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
})
afterEach(() => {
  server.resetHandlers()
})
afterAll(() => {
  server.close()
})

function envelope(message: string): ErrorEnvelope {
  return { error: { code: 'service_unavailable', message, detail: null } }
}

function players() {
  return unwrap(api.GET('/api/players'))
}

describe('retryDelay', () => {
  it('starts short, because the Pi is one hop away', () => {
    expect(retryDelay(0)).toBe(RETRY_BASE_MS)
  })

  it('doubles', () => {
    expect(retryDelay(1)).toBe(RETRY_BASE_MS * 2)
    expect(retryDelay(2)).toBe(RETRY_BASE_MS * 4)
  })

  it('caps, so a long outage does not back off into next week', () => {
    expect(retryDelay(50)).toBe(RETRY_CAP_MS)
  })
})

describe('shouldRetry', () => {
  it('gives up after the attempt budget', () => {
    const error = new OfflineError(new TypeError('Failed to fetch'))
    expect(shouldRetry(MAX_ATTEMPTS - 1, error)).toBe(true)
    expect(shouldRetry(MAX_ATTEMPTS, error)).toBe(false)
  })

  it('never retries what would fail identically', () => {
    const notFound = new ApiError(404, {
      error: { code: 'not_found', message: 'Not Found', detail: null },
    })
    expect(shouldRetry(0, notFound)).toBe(false)
  })
})

describe('a query against a flaky Pi', () => {
  it('retries until it gets through', async () => {
    let attempts = 0
    server.use(
      http.get('*/api/players', () => {
        attempts += 1
        if (attempts < 3)
          return HttpResponse.json(envelope('The database is unavailable'), {
            status: 503,
          })
        return HttpResponse.json([
          { id: 1, display_name: 'Ada', is_archived: false, created_at: '2026-09-23T10:00:00Z' },
        ])
      }),
    )

    const client = createQueryClient()
    const result = await client.fetchQuery({ queryKey: ['players'], queryFn: players })

    expect(attempts).toBe(3)
    expect(result[0]?.display_name).toBe('Ada')
  })

  it('stops after the budget and surfaces the last failure', async () => {
    let attempts = 0
    server.use(
      http.get('*/api/players', () => {
        attempts += 1
        return HttpResponse.error()
      }),
    )

    const client = createQueryClient()
    const failure = await client.fetchQuery({ queryKey: ['players'], queryFn: players }).then(
      () => null,
      (error: unknown) => error,
    )

    expect(failure).toBeInstanceOf(OfflineError)
    expect(attempts).toBe(MAX_ATTEMPTS + 1) // the first try plus its retries
  })

  it('does not retry a refusal', async () => {
    let attempts = 0
    server.use(
      http.get('*/api/players', () => {
        attempts += 1
        return HttpResponse.json(
          { error: { code: 'not_found', message: 'Not Found', detail: null } },
          { status: 404 },
        )
      }),
    )

    const client = createQueryClient()
    await client.fetchQuery({ queryKey: ['players'], queryFn: players }).catch(() => null)

    expect(attempts).toBe(1)
  })
})

describe('a mutation', () => {
  it('is never retried, because a recorded dart is not idempotent', async () => {
    let attempts = 0
    server.use(
      http.post('*/api/players', () => {
        attempts += 1
        return HttpResponse.error()
      }),
    )

    const client = createQueryClient()
    await client
      .getMutationCache()
      .build(client, {
        mutationFn: () => unwrap(api.POST('/api/players', { body: { display_name: 'Ada' } })),
      })
      .execute(undefined)
      .catch(() => null)

    expect(attempts).toBe(1)
  })
})

describe('what the caches tell the connection monitor', () => {
  it('a query that never gets an answer loses the connection', async () => {
    server.use(http.get('*/api/players', () => HttpResponse.error()))
    const monitor = new ConnectionMonitor()
    const client = createQueryClient(monitor)

    await client.fetchQuery({ queryKey: ['players'], queryFn: players }).catch(() => null)

    expect(monitor.reachable).toBe(false)
  })

  it('and a later success gets it back', async () => {
    const monitor = new ConnectionMonitor()
    const client = createQueryClient(monitor)

    server.use(http.get('*/api/players', () => HttpResponse.error()))
    await client.fetchQuery({ queryKey: ['a'], queryFn: players }).catch(() => null)
    expect(monitor.reachable).toBe(false)

    server.use(http.get('*/api/players', () => HttpResponse.json([])))
    await client.fetchQuery({ queryKey: ['b'], queryFn: players })

    expect(monitor.reachable).toBe(true)
  })

  it('a refusal leaves the connection alone', async () => {
    server.use(
      http.get('*/api/players', () =>
        HttpResponse.json(
          { error: { code: 'not_found', message: 'Not Found', detail: null } },
          { status: 404 },
        ),
      ),
    )
    const monitor = new ConnectionMonitor()
    const client = createQueryClient(monitor)

    await client.fetchQuery({ queryKey: ['players'], queryFn: players }).catch(() => null)

    expect(monitor.reachable).toBe(true)
  })
})

describe('the defaults a screen inherits', () => {
  it('trusts a fetched answer briefly, so navigation does not re-request everything', () => {
    const queries = createQueryClient().getDefaultOptions().queries
    expect(queries?.staleTime).toBe(STALE_TIME_MS)
  })

  it('refetches when the phone comes back from a lock screen or a dropped network', () => {
    const queries = createQueryClient().getDefaultOptions().queries
    expect(queries?.refetchOnWindowFocus).toBe(true)
    expect(queries?.refetchOnReconnect).toBe(true)
  })
})
