/**
 * The route table, opened at each address the way a phone would.
 *
 * Every test mounts `App` under a `MemoryRouter` pointed at one URL, which is
 * exactly the situation a cold reload creates: #16's SPA fallback hands
 * `index.html` to any non-`/api` path, the app boots with no history behind
 * it, and the client alone decides what that path means.
 *
 * Since #22 two of those addresses fetch, so the mount goes through
 * `renderApp`, which supplies the query client the app supplies, in front of a
 * Pi that answers. What each screen does with the answer is its own test file's
 * business; this one is about which screen a path reaches.
 *
 * React reports most of what it dislikes -- bad keys, invalid props, bad
 * nesting -- through `console.error` rather than by throwing, so "did not
 * throw" is not enough and every test also checks the console stayed clean.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import process from 'node:process'
import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import type { MockInstance } from 'vitest'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installServer, renderApp, server } from './test-harness'

installServer()

let consoleError: MockInstance<typeof console.error>
let consoleWarn: MockInstance<typeof console.warn>

beforeEach(() => {
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
  consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  // An empty box. Nothing here is about what the screens show, only about
  // which screen is mounted, so every answer is the emptiest legal one.
  server.use(
    http.get('*/api/matches', () =>
      HttpResponse.json({ items: [], total: 0, limit: 1, offset: 0 }),
    ),
    http.get('*/api/players', () => HttpResponse.json([])),
  )
})

afterEach(() => {
  vi.restoreAllMocks()
})

function expectCleanConsole() {
  expect(consoleError).not.toHaveBeenCalled()
  expect(consoleWarn).not.toHaveBeenCalled()
}

const openAt = renderApp

describe('the screens #22 built', () => {
  it.each([
    ['/', 'Darts'],
    ['/players', 'Players'],
  ])('%s routes to the real %s screen, not a placeholder', async (path, title) => {
    openAt(path)
    expect(await screen.findByRole('heading', { name: title })).toBeInTheDocument()
    expect(screen.queryByText(/waiting on/i)).not.toBeInTheDocument()
    expectCleanConsole()
  })
})

describe('the screens #23 onwards will fill in', () => {
  it.each([
    ['/setup', 'New match', '#23'],
    ['/history', 'History', '#26'],
    ['/stats', 'Stats', '#27'],
  ])('%s routes to %s', (path, title, ticket) => {
    openAt(path)
    expect(screen.getByRole('heading', { name: title })).toBeInTheDocument()
    expect(screen.getByText(`Waiting on ${ticket}`)).toBeInTheDocument()
    expectCleanConsole()
  })
})

describe('deep links', () => {
  // The acceptance criterion: "reloading on a deep link like /history/42
  // loads that screen". The server half is tests/api/test_static.py; this is
  // the client half, and together they are the whole round trip.
  it('/history/42 loads the match screen, not the history list', () => {
    openAt('/history/42')
    expect(screen.getByRole('heading', { name: 'Match' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'History' })).not.toBeInTheDocument()
    expectCleanConsole()
  })

  it('/play/7 loads the play screen', () => {
    openAt('/play/7')
    expect(screen.getByRole('heading', { name: 'Play' })).toBeInTheDocument()
    expectCleanConsole()
  })

  it.each(['/history/42', '/play/7', '/history/99999'])('%s renders inside the shell', (path) => {
    openAt(path)
    // The layout wraps every route, deep links included -- which is what
    // holds the content clear of the Dynamic Island on a reload.
    expect(document.querySelector('.app-shell__content')).not.toBeNull()
    expectCleanConsole()
  })
})

describe('an address that means nothing', () => {
  it('says so instead of rendering an empty shell', () => {
    openAt('/nonsense')
    expect(screen.getByRole('heading', { name: /no such screen/i })).toBeInTheDocument()
    expectCleanConsole()
  })

  it('offers a way back', () => {
    openAt('/nonsense')
    expect(screen.getByRole('link', { name: /back to the scoreboard/i })).toHaveAttribute(
      'href',
      '/',
    )
    expectCleanConsole()
  })
})

describe('/style, kept from #4', () => {
  it('still renders the style guide', () => {
    openAt('/style')
    expect(screen.getByRole('heading', { name: /darts design system/i })).toBeInTheDocument()
    expectCleanConsole()
  })

  it('renders every component section', () => {
    openAt('/style')
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
    openAt('/style')
    const disabled = screen.getAllByRole('button').filter((b) => b.hasAttribute('disabled'))
    expect(disabled.length).toBeGreaterThan(0)
    expect(document.querySelectorAll('[data-pressed="true"]').length).toBeGreaterThan(0)
    expectCleanConsole()
  })

  it.each([
    ['/style/x01', /501 · Leg 2 of 5/],
    ['/style/cricket', /Cricket · Standard/],
    ['/style/setup', /New match/],
  ])('%s renders the mockup', (path, context) => {
    openAt(path)
    expect(screen.getByText(context)).toBeInTheDocument()
    expectCleanConsole()
  })
})

describe('the root layout', () => {
  it('gives every route a content element to be inset', () => {
    openAt('/')
    expect(document.querySelector('.app-shell__content')).not.toBeNull()
  })

  it('insets that element by all four safe-area tokens', () => {
    // Asserted against the stylesheet's text, not `getComputedStyle`, because
    // jsdom does not implement `env()` and resolves every inset to 0 -- so a
    // computed-style assertion here would pass just as happily against a rule
    // that had no padding at all. tokens.test.ts reads tokens.css the same
    // way, for the same reason.
    //
    // #4 put viewport-fit=cover in index.html and these tokens in tokens.css.
    // Without something applying them the app paints *under* the Dynamic
    // Island, which is strictly worse than not setting viewport-fit at all.
    // Vitest runs with cwd at the frontend package root; import.meta.url is
    // an http URL under the Vite transform and cannot reach the disk.
    const css = readFileSync(resolve(process.cwd(), 'src/routes/RootLayout.css'), 'utf8')
    const rule = /\.app-shell__content\s*\{([^}]*)\}/.exec(css)?.[1]
    expect(rule, '.app-shell__content is not in RootLayout.css').toBeDefined()
    for (const token of ['--safe-top', '--safe-right', '--safe-bottom', '--safe-left']) {
      expect(rule).toContain(`var(${token})`)
    }
  })

  it('wraps every route in exactly one shell', () => {
    // A connection toast living inside a screen would unmount on navigation
    // -- exactly when somebody tried to navigate away from the problem.
    openAt('/')
    expect(document.querySelectorAll('.app-shell').length).toBe(1)
  })
})
