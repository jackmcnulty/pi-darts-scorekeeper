/**
 * The one way this app talks to the Pi.
 *
 * Every request and response type here comes from `schema.d.ts`, which is
 * generated from the server's own OpenAPI document by `scripts/gen-api.mjs`
 * and checked for staleness by CI. Nothing in this file, or in any screen
 * built on it, describes a payload by hand.
 *
 * That was only half true until #21. FastAPI documented every failure as its
 * own `HTTPValidationError` while the server had been sending #16's envelope
 * since #16, so the generated client described an error shape no route
 * returns. `darts.api.openapi` now publishes `ErrorEnvelope` on every
 * operation, which is why `ApiError` below can be built entirely out of
 * generated types instead of a hand-written guess.
 *
 * Failures arrive as exceptions rather than as a branch of the return value:
 * TanStack Query decides what to retry by catching, and a screen that forgot
 * to check an `error` field would render `undefined` instead of saying
 * something went wrong.
 */
import createClient from 'openapi-fetch'
import type { components, paths } from './schema'

/** The body of every failed `/api` response. Generated, not written. */
export type ErrorEnvelope = components['schemas']['ErrorEnvelope']

/** The closed vocabulary `error.code` is drawn from. Generated, not written. */
export type ErrorCode = components['schemas']['ErrorCode']

/**
 * What `openapi-fetch` resolves to: the payload, or the envelope, never both.
 *
 * This describes the *wrapper*, not a payload -- `D` is always some generated
 * type -- so it is not the kind of hand-written response type the rule above
 * forbids. It is spelled out rather than imported so that `unwrap` can infer
 * `D` from the call site.
 */
type Settled<D> =
  | { data: D; error?: undefined; response: Response }
  | { data?: undefined; error: ErrorEnvelope; response: Response }

/** The server answered, and the answer was a refusal. */
export class ApiError extends Error {
  readonly status: number
  readonly code: ErrorCode
  readonly detail: unknown

  constructor(status: number, envelope: ErrorEnvelope) {
    super(envelope.error.message)
    this.name = 'ApiError'
    this.status = status
    this.code = envelope.error.code
    this.detail = envelope.error.detail
  }
}

/**
 * The request never got an answer.
 *
 * Wi-Fi dropped, the Pi is off, or the phone woke up on a different network.
 * Distinct from `ApiError` because it is the only failure that means "we
 * cannot see the server", which is what raises the connection toast -- and
 * because it is always worth retrying, where most `ApiError`s never are.
 */
export class OfflineError extends Error {
  constructor(cause: unknown) {
    super('Cannot reach the scoreboard', { cause })
    this.name = 'OfflineError'
  }
}

/**
 * Await a call and return its payload, throwing anything else.
 *
 * ```ts
 * const players = await unwrap(api.GET('/api/players'))
 * ```
 */
export async function unwrap<D>(pending: Promise<Settled<D>>): Promise<D> {
  let settled: Settled<D>
  try {
    settled = await pending
  } catch (cause) {
    // `fetch` rejects only when the request never completed. A 500 is a
    // resolved response; this is the wire being gone.
    throw new OfflineError(cause)
  }
  if (settled.error !== undefined) {
    throw new ApiError(settled.response.status, settled.error)
  }
  return settled.data
}

/**
 * Is this worth trying again?
 *
 * A 4xx is the request's own fault and will fail identically forever, so
 * retrying one just delays the message the user needs. Everything else --
 * an unreachable Pi, a 500, the 503 a locked database returns -- is a
 * condition of the moment.
 */
export function isRetryable(error: unknown): boolean {
  if (error instanceof OfflineError) return true
  if (error instanceof ApiError) return error.status >= 500
  return false
}

/**
 * The `reason` discriminator, when there is one.
 *
 * Several distinct refusals share the 409 `conflict` code -- "that leg is
 * already won", "there is nothing to undo" -- and the server tells them apart
 * with `detail.reason` so a client never has to match on the message text.
 * `detail` is `unknown` by construction, since its shape depends on the code,
 * so reaching into it is narrowed in exactly one place: here.
 */
export function reasonOf(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null
  const detail: unknown = error.detail
  if (typeof detail !== 'object' || detail === null || !('reason' in detail)) return null
  const reason: unknown = detail.reason
  return typeof reason === 'string' ? reason : null
}

/**
 * Same origin as whatever served the app: the Pi serves the API and the app
 * together, and in development Vite proxies `/api` through to Uvicorn. Either
 * way nothing here has to know an address, and there is no configuration to
 * get wrong on a box that moves between networks.
 *
 * The origin is spelled out rather than left implicit because `openapi-fetch`
 * constructs a `Request`, and outside a browser -- jsdom under Vitest, where
 * `fetch` comes from Node -- a relative URL is an `ERR_INVALID_URL` rather
 * than something resolved against the page. In a browser the two are the
 * same request.
 */
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  // `openapi-fetch` reads `globalThis.fetch` once, when the client is
  // constructed -- which here is module load, the earliest moment there is.
  // Looking it up per call instead costs nothing in the browser and is what
  // lets a test install MSW after importing this module, rather than having
  // to intercept the network before the import that triggers it.
  fetch: (request) => fetch(request),
})
