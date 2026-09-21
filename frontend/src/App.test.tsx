import { render, screen } from '@testing-library/react'
import type { MockInstance } from 'vitest'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

/**
 * The /style route has to render every component in every state without
 * throwing and without React complaining. React reports most of what it
 * dislikes — bad keys, invalid props, bad nesting — through console.error
 * rather than by throwing, so a plain "did not throw" assertion would miss it.
 */
let consoleError: MockInstance<typeof console.error>
let consoleWarn: MockInstance<typeof console.warn>

beforeEach(() => {
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
  window.location.hash = ''
})

function expectCleanConsole() {
  expect(consoleError).not.toHaveBeenCalled()
  expect(consoleWarn).not.toHaveBeenCalled()
}

describe('/style', () => {
  it('renders the style guide without throwing', () => {
    expect(() => render(<App />)).not.toThrow()
    expect(screen.getByRole('heading', { name: /darts design system/i })).toBeInTheDocument()
    expectCleanConsole()
  })

  it('renders every component section', () => {
    render(<App />)
    for (const name of [
      'Surfaces',
      'Type scale',
      'Button',
      'Key',
      'Chip',
      'SegmentedControl',
      'Stepper',
      'ScoreCard',
      'Toast',
      'Sheet',
      'Screen mockups',
    ]) {
      expect(screen.getByRole('heading', { name })).toBeInTheDocument()
    }
    expectCleanConsole()
  })

  it('renders disabled and pressed states', () => {
    render(<App />)
    const disabled = screen.getAllByRole('button').filter((b) => b.hasAttribute('disabled'))
    expect(disabled.length).toBeGreaterThan(0)
    const pressed = document.querySelectorAll('[data-pressed="true"]')
    expect(pressed.length).toBeGreaterThan(0)
    expectCleanConsole()
  })
})

describe('mockups', () => {
  it.each([
    ['#x01', /501 · Leg 2 of 5/],
    ['#cricket', /Cricket · Standard/],
    ['#setup', /New match/],
  ])('%s renders without throwing', (hash, context) => {
    window.location.hash = hash
    expect(() => render(<App />)).not.toThrow()
    expect(screen.getByText(context)).toBeInTheDocument()
    expectCleanConsole()
  })

  it('an unknown hash falls back to the style guide', () => {
    window.location.hash = '#nonsense'
    render(<App />)
    expect(screen.getByRole('heading', { name: /darts design system/i })).toBeInTheDocument()
    expectCleanConsole()
  })
})
