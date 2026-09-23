/**
 * The toast follows the app's own monitor, so these tests drive that monitor.
 *
 * `ConnectionToast` reads the singleton rather than taking one as a prop: it
 * is mounted once, by the root layout, and a prop would only ever be threaded
 * down from there. The singleton is restored between tests by recording a
 * success, which is the same thing a recovered request would do.
 */
import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { OfflineError } from '../api/client'
import { connection } from '../api/connection'
import { ConnectionToast } from './ConnectionToast'

function goOffline() {
  act(() => {
    connection.recordFailure(new OfflineError(new TypeError('Failed to fetch')))
  })
}

function goOnline() {
  act(() => {
    connection.recordSuccess()
  })
}

afterEach(() => {
  connection.recordSuccess()
})

describe('while the Pi is reachable', () => {
  it('shows nothing at all', () => {
    render(<ConnectionToast />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})

describe('when the connection drops', () => {
  it('says so, without being remounted', () => {
    render(<ConnectionToast />)
    goOffline()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('says what is happening and what is being done about it', () => {
    render(<ConnectionToast />)
    goOffline()
    expect(screen.getByText(/lost the scoreboard/i)).toBeInTheDocument()
    expect(screen.getByText(/retrying/i)).toBeInTheDocument()
  })

  it('announces itself politely rather than interrupting', () => {
    // #4's Toast is role=status / aria-live=polite: a player mid-throw should
    // not have VoiceOver cut across them.
    render(<ConnectionToast />)
    goOffline()
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite')
  })

  it('does not swallow taps meant for the board behind it', () => {
    render(<ConnectionToast />)
    goOffline()
    const layer = document.querySelector('.connection-toast')
    expect(layer).not.toBeNull()
    expect(getComputedStyle(layer as Element).pointerEvents).toBe('none')
  })
})

describe('when it comes back', () => {
  it('clears itself with no dismiss button to press', () => {
    render(<ConnectionToast />)
    goOffline()
    expect(screen.getByRole('status')).toBeInTheDocument()

    goOnline()

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
