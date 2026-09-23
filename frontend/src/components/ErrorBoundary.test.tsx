/**
 * The boundary catches a thrown render and offers a way out.
 *
 * React writes a caught error to `console.error` regardless of whether a
 * boundary handled it, so every test here silences it deliberately. A test
 * that let it through would pass while filling the run with the traceback of
 * an error it meant to catch.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from './ErrorBoundary'

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
})

function Boom(): never {
  throw new Error('the keypad exploded')
}

function Fine() {
  return <p>the board</p>
}

describe('when nothing is wrong', () => {
  it('renders its children and stays out of the way', () => {
    render(
      <ErrorBoundary onError={vi.fn()}>
        <Fine />
      </ErrorBoundary>,
    )

    expect(screen.getByText('the board')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('when a render throws', () => {
  it('shows a message instead of a white screen', () => {
    render(
      <ErrorBoundary onError={vi.fn()}>
        <Boom />
      </ErrorBoundary>,
    )

    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /something broke/i })).toBeInTheDocument()
  })

  it('quotes the error, so a player can say what it said', () => {
    render(
      <ErrorBoundary onError={vi.fn()}>
        <Boom />
      </ErrorBoundary>,
    )

    expect(screen.getByText('the keypad exploded')).toBeInTheDocument()
  })

  it('says the score is safe, because it is', () => {
    // The server is authoritative for all game state. A render bug on the
    // phone has not lost a single dart, and the screen should say so.
    render(
      <ErrorBoundary onError={vi.fn()}>
        <Boom />
      </ErrorBoundary>,
    )

    expect(screen.getByText(/the server keeps the score/i)).toBeInTheDocument()
  })

  it('offers a recovery affordance', () => {
    render(
      <ErrorBoundary onError={vi.fn()}>
        <Boom />
      </ErrorBoundary>,
    )

    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument()
  })

  it('reports the error and where it came from', () => {
    const onError = vi.fn()
    render(
      <ErrorBoundary onError={onError}>
        <Boom />
      </ErrorBoundary>,
    )

    expect(onError).toHaveBeenCalledOnce()
    const [error, info] = onError.mock.calls[0] as [Error, { componentStack?: string | null }]
    expect(error.message).toBe('the keypad exploded')
    expect(info.componentStack).toBeTruthy()
  })

  it('logs to the console when nothing else is listening', () => {
    // There is no error reporting service on a LAN-only box. Safari's console
    // is the whole story, so the default must actually write to it.
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    )

    expect(console.error).toHaveBeenCalledWith(
      'render failed',
      expect.objectContaining({ message: 'the keypad exploded' }),
      expect.anything(),
    )
  })
})

describe('recovering', () => {
  it('"Try again" clears the caught error and re-renders the subtree', () => {
    let shouldThrow = true

    function Sometimes() {
      if (shouldThrow) throw new Error('transient')
      return <p>the board</p>
    }

    render(
      <ErrorBoundary onError={vi.fn()}>
        <Sometimes />
      </ErrorBoundary>,
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()

    shouldThrow = false
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(screen.getByText('the board')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('"Reload" reloads the page', () => {
    const reload = vi.fn()
    // jsdom's location.reload is not writable, so the whole property is
    // replaced -- and put back, because every other test in this run shares
    // this window.
    const original = Object.getOwnPropertyDescriptor(window, 'location')
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, reload },
    })

    try {
      render(
        <ErrorBoundary onError={vi.fn()}>
          <Boom />
        </ErrorBoundary>,
      )
      fireEvent.click(screen.getByRole('button', { name: 'Reload' }))

      expect(reload).toHaveBeenCalledOnce()
    } finally {
      if (original) Object.defineProperty(window, 'location', original)
    }
  })
})
