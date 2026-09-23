/**
 * What the client does with the answers the Pi actually gives.
 *
 * The bodies below are copied from real responses recorded against this
 * repository's server, not invented: the envelope, the `reason` discriminator
 * on a 409, and pydantic's `loc`-carrying list on a 422. Fixtures that drifted
 * from the server would make these tests agree with themselves and with
 * nothing else, which is the exact failure #21 found in the generated schema.
 */
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { ApiError, api, isRetryable, OfflineError, reasonOf, unwrap } from './client'
import type { ErrorCode, ErrorEnvelope } from './client'

const server = setupServer()

beforeAll(() => {
  // Anything a test did not ask for is a mistake in the test, not a fall
  // through to a real network the CI runner does not have.
  server.listen({ onUnhandledRequest: 'error' })
})
afterEach(() => {
  server.resetHandlers()
})
afterAll(() => {
  server.close()
})

function envelope(code: ErrorCode, message: string, detail: unknown = null): ErrorEnvelope {
  return { error: { code, message, detail } }
}

/**
 * The reason a call rejected, or a failure saying it did not.
 *
 * `.catch(e => e as ApiError)` would widen the promise to `payload | ApiError`
 * and quietly let a test assert on a success it meant to prove impossible.
 */
async function failureOf(pending: Promise<unknown>): Promise<unknown> {
  return pending.then(
    (value) => {
      throw new Error(`expected a failure, but the call resolved with ${JSON.stringify(value)}`)
    },
    (error: unknown) => error,
  )
}

describe('a request that succeeds', () => {
  it('returns the payload, typed from the schema', async () => {
    server.use(
      http.get('*/api/players', () =>
        HttpResponse.json([
          { id: 1, display_name: 'Ada', is_archived: false, created_at: '2026-09-23T10:00:00Z' },
        ]),
      ),
    )

    const players = await unwrap(api.GET('/api/players'))

    // `display_name` resolves through `paths` -> `PlayerResponse`. If the
    // generated type were lost, this line would stop compiling, which is the
    // assertion that matters as much as the value.
    expect(players[0]?.display_name).toBe('Ada')
  })

  it('sends a typed body and reads the created resource back', async () => {
    server.use(
      http.post('*/api/players', async ({ request }) => {
        const body = await request.json()
        expect(body).toEqual({ display_name: 'Grace' })
        return HttpResponse.json(
          { id: 2, display_name: 'Grace', is_archived: false, created_at: '2026-09-23T10:00:00Z' },
          { status: 201 },
        )
      }),
    )

    const created = await unwrap(api.POST('/api/players', { body: { display_name: 'Grace' } }))

    expect(created.id).toBe(2)
  })
})

describe('a request the server refuses', () => {
  it('throws ApiError carrying the status and the code', async () => {
    server.use(
      http.get('*/api/matches/4242', () =>
        HttpResponse.json(
          envelope('not_found', 'Match 4242 does not exist', { reason: 'not_found' }),
          {
            status: 404,
          },
        ),
      ),
    )

    const call = unwrap(
      api.GET('/api/matches/{match_id}', { params: { path: { match_id: 4242 } } }),
    )
    const error = await failureOf(call)

    expect(error).toBeInstanceOf(ApiError)
    const refusal = error as ApiError
    expect(refusal.status).toBe(404)
    expect(refusal.code).toBe('not_found')
    expect(refusal.message).toBe('Match 4242 does not exist')
  })

  it('keeps pydantic’s per-field errors intact under detail', async () => {
    const fields = [{ type: 'missing', loc: ['body', 'display_name'], msg: 'Field required' }]
    server.use(
      http.post('*/api/players', () =>
        HttpResponse.json(envelope('validation_error', 'Request validation failed', fields), {
          status: 422,
        }),
      ),
    )

    const error = await failureOf(unwrap(api.POST('/api/players', { body: { display_name: '' } })))

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('validation_error')
    expect((error as ApiError).detail).toEqual(fields)
  })

  it('exposes the reason that tells two 409s apart', async () => {
    server.use(
      http.post('*/api/players', () =>
        HttpResponse.json(
          envelope('conflict', 'A player called Ada already exists', {
            reason: 'duplicate_name',
          }),
          { status: 409 },
        ),
      ),
    )

    const error = await failureOf(
      unwrap(api.POST('/api/players', { body: { display_name: 'Ada' } })),
    )

    expect(reasonOf(error)).toBe('duplicate_name')
  })
})

describe('reasonOf', () => {
  it('is null for anything that is not an ApiError', () => {
    expect(reasonOf(new Error('boom'))).toBeNull()
    expect(reasonOf(null)).toBeNull()
  })

  it('is null when the code carries no reason', () => {
    expect(reasonOf(new ApiError(500, envelope('internal', 'x')))).toBeNull()
  })

  it('is null when detail is the wrong shape for a reason', () => {
    const listDetail = envelope('validation_error', 'x', [{ loc: ['body'] }])
    expect(reasonOf(new ApiError(422, listDetail))).toBeNull()
    const numeric = envelope('conflict', 'x', { reason: 7 })
    expect(reasonOf(new ApiError(409, numeric))).toBeNull()
  })
})

describe('a request that never arrives', () => {
  it('throws OfflineError, not ApiError', async () => {
    server.use(http.get('*/api/healthz', () => HttpResponse.error()))

    const error = await failureOf(unwrap(api.GET('/api/healthz')))

    expect(error).toBeInstanceOf(OfflineError)
    expect(error).not.toBeInstanceOf(ApiError)
  })

  it('keeps the underlying failure as the cause', async () => {
    server.use(http.get('*/api/healthz', () => HttpResponse.error()))

    const error = (await failureOf(unwrap(api.GET('/api/healthz')))) as OfflineError

    expect(error.cause).toBeInstanceOf(Error)
  })
})

describe('isRetryable', () => {
  it('retries an unreachable server', () => {
    expect(isRetryable(new OfflineError(new Error('failed to fetch')))).toBe(true)
  })

  it.each([500, 503])('retries a %d, which is a condition of the moment', (status) => {
    expect(isRetryable(new ApiError(status, envelope('internal', 'x')))).toBe(true)
  })

  it.each([400, 404, 409, 422])('never retries a %d, which would fail identically', (status) => {
    const body = envelope('invalid_request', 'x')
    expect(isRetryable(new ApiError(status, body))).toBe(false)
  })

  it('does not retry a thrown render bug that reached the query layer', () => {
    expect(isRetryable(new TypeError('x is not a function'))).toBe(false)
  })
})
