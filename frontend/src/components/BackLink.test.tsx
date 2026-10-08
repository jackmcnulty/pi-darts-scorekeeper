/**
 * The shared ‹ (#70): what it promises on every screen, in one place.
 *
 * Each screen's own test proves its ‹ is there, named and aimed, through
 * `expectBackLink`. What this file proves is the part no single screen can: that
 * there is one ‹ and one floor. The two hand-copies #70 replaced had already
 * drifted apart, so the tests here are about drift.
 *
 * The floor is asserted against the stylesheet as text, which is #25's `rule()`
 * pattern from `CricketBoard.test.tsx`: jsdom does no layout, so a bounding box
 * is zero. The real box is measured in a browser at 402×781 for the PR.
 */
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import { BackLink } from './BackLink'

const read = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8')

/** Every declaration in every block whose selector is exactly `selector`. */
const rule = (selector: string, sheet: string) =>
  [...sheet.matchAll(new RegExp(`^\\${selector}\\s*\\{([^}]*)\\}`, 'gm'))]
    .map((match) => match[1] ?? '')
    .join('\n')

const ROUTES = 'src/routes'

describe('the link', () => {
  it('names where it goes and draws only the glyph', () => {
    render(
      <MemoryRouter>
        <BackLink to="/history" label="Back to history" className="detail__back" />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Back to history' })
    expect(link).toHaveAttribute('href', '/history')
    expect(link).toHaveTextContent(/^‹$/)
    // The screen's class is added to the shared one, not swapped for it.
    expect(link).toHaveClass('back-link', 'detail__back')
  })
})

describe('the 56px floor', () => {
  it('is a minimum on both axes, so the box can grow but never shrink', () => {
    const own = rule('.back-link', read('src/components/BackLink.css'))

    expect(own).toMatch(/min-width:\s*var\(--touch-min\)/)
    expect(own).toMatch(/min-height:\s*var\(--touch-min\)/)
    // A fixed size or a maximum is how the play frame's copy differed; either
    // would let a later edit take the box under the floor.
    expect(own).not.toMatch(/(^|[^-])(width|height):/m)
    expect(own).not.toMatch(/max-(width|height):/)
  })

  it('is not overridden by any screen: a screen owns only its gutter pull', () => {
    const sheets = readdirSync(resolve(process.cwd(), ROUTES)).filter((name) =>
      name.endsWith('.css'),
    )
    const pulls = sheets.flatMap((name) => {
      const sheet = read(`${ROUTES}/${name}`)
      return [...sheet.matchAll(/^(\.[a-z]+__back)\s*\{([^}]*)\}/gm)].map((match) => ({
        name,
        selector: match[1],
        body: match[2] ?? '',
      }))
    })

    // Players, History, Stats, MatchDetail, PlayerStats, Setup and Play.
    expect(pulls.map((pull) => pull.name).sort()).toEqual([
      'History.css',
      'MatchDetail.css',
      'Play.css',
      'PlayerStats.css',
      'Players.css',
      'Setup.css',
      'Stats.css',
    ])
    for (const pull of pulls) {
      expect(pull.body, `${pull.name} ${String(pull.selector)}`).not.toMatch(
        /width|height|font-size|padding/,
      )
    }
  })
})

describe('one copy', () => {
  it('leaves no hand-drawn ‹ on any screen', () => {
    // A line of JSX text that is the glyph and nothing else is how #23 and #24
    // each drew theirs. Comments and test names may mention it; markup may not.
    const screens = readdirSync(resolve(process.cwd(), ROUTES)).filter(
      (name) => name.endsWith('.tsx') && !name.endsWith('.test.tsx'),
    )
    const copies = screens.filter((name) =>
      read(`${ROUTES}/${name}`)
        .split('\n')
        .some((line) => line.trim() === '‹'),
    )
    expect(copies).toEqual([])
  })
})
