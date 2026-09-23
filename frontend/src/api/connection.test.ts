/**
 * The monitor decides reachability from evidence, so every test supplies some.
 *
 * Nothing here touches `navigator.onLine`, because the monitor does not: see
 * the module docstring for why a phone associated with an access point tells
 * you nothing about whether the Pi is answering.
 */
import { describe, expect, it, vi } from 'vitest'
import { ApiError, OfflineError, type ErrorEnvelope } from './client'
import { ConnectionMonitor, connection } from './connection'

const refused: ErrorEnvelope = {
  error: { code: 'not_found', message: 'Not Found', detail: null },
}

function offline(): OfflineError {
  return new OfflineError(new TypeError('Failed to fetch'))
}

describe('a fresh monitor', () => {
  it('assumes the Pi is reachable until something says otherwise', () => {
    // The alternative is a connection-lost toast on every cold start, before
    // a single request has been made.
    expect(new ConnectionMonitor().reachable).toBe(true)
  })
})

describe('losing the connection', () => {
  it('an unanswered request means the Pi cannot be seen', () => {
    const monitor = new ConnectionMonitor()
    monitor.recordFailure(offline())
    expect(monitor.reachable).toBe(false)
  })

  it('a refusal does not, because a server that refuses is plainly there', () => {
    const monitor = new ConnectionMonitor()
    monitor.recordFailure(new ApiError(404, refused))
    expect(monitor.reachable).toBe(true)
  })

  it('clears a lost connection when any answer arrives, even a refusal', () => {
    const monitor = new ConnectionMonitor()
    monitor.recordFailure(offline())
    monitor.recordFailure(new ApiError(409, refused))
    expect(monitor.reachable).toBe(true)
  })

  it('recovers on a success', () => {
    const monitor = new ConnectionMonitor()
    monitor.recordFailure(offline())
    expect(monitor.reachable).toBe(false)
    monitor.recordSuccess()
    expect(monitor.reachable).toBe(true)
  })
})

describe('notifying subscribers', () => {
  it('tells them when reachability changes', () => {
    const monitor = new ConnectionMonitor()
    const listener = vi.fn()
    monitor.subscribe(listener)

    monitor.recordFailure(offline())
    monitor.recordSuccess()

    expect(listener).toHaveBeenCalledTimes(2)
  })

  it('stays quiet when it does not', () => {
    // A dart is a mutation and a refetch. Re-rendering the shell on each one
    // to announce that nothing changed is a re-render per throw.
    const monitor = new ConnectionMonitor()
    const listener = vi.fn()
    monitor.subscribe(listener)

    monitor.recordSuccess()
    monitor.recordSuccess()
    monitor.recordFailure(new ApiError(404, refused))

    expect(listener).not.toHaveBeenCalled()
  })

  it('stops telling them once unsubscribed', () => {
    const monitor = new ConnectionMonitor()
    const listener = vi.fn()
    const unsubscribe = monitor.subscribe(listener)

    unsubscribe()
    monitor.recordFailure(offline())

    expect(listener).not.toHaveBeenCalled()
  })

  it('reports the current state through getSnapshot', () => {
    const monitor = new ConnectionMonitor()
    expect(monitor.getSnapshot()).toBe(true)
    monitor.recordFailure(offline())
    expect(monitor.getSnapshot()).toBe(false)
  })
})

describe('the app’s own monitor', () => {
  it('exists and starts reachable', () => {
    expect(connection).toBeInstanceOf(ConnectionMonitor)
    expect(connection.reachable).toBe(true)
  })
})
