/**
 * The palette helpers, including the one that has to agree with the server.
 *
 * `suggest` is a second implementation of `darts.repo.players.next_accent_index`
 * and the cases below are the ones where a wrong copy would be visible: an
 * empty list, a partly-used palette, a full one, and a player who is being
 * edited and should not count as clashing with themselves.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import process from 'node:process'
import { describe, expect, it } from 'vitest'
import type { Player } from '../api/players'
import { ACCENT_COUNT, ACCENTS, accentColour, holdersOf, suggest } from './accents'

function player(id: number, accent_index: number | null, over: Partial<Player> = {}): Player {
  return {
    id,
    display_name: `P${id}`,
    short_name: null,
    accent_index,
    is_archived: false,
    created_at: '2026-09-23T19:00:00.000Z',
    ...over,
  }
}

describe('the palette itself', () => {
  it('is the one tokens.css declares', () => {
    // Read as text rather than through getComputedStyle: this is about how many
    // accents exist, and a missing declaration resolves to '' either way.
    // Vitest's cwd is the package root; import.meta.url is an http URL under
    // the Vite transform and cannot reach the disk.
    const css = readFileSync(resolve(process.cwd(), 'src/styles/tokens.css'), 'utf8')
    const declared = [...css.matchAll(/^\s*--accent-(\d+):/gm)].map((match) => Number(match[1]))
    expect(declared.sort((a, b) => a - b)).toEqual([...ACCENTS])
    expect(ACCENT_COUNT).toBe(8)
  })

  it('names a colour as a token reference, and nothing at all for no colour', () => {
    expect(accentColour(3)).toBe('var(--accent-3)')
    expect(accentColour(null)).toBeUndefined()
    expect(accentColour(undefined)).toBeUndefined()
  })
})

describe('who holds what', () => {
  it('ignores archived players and players with no colour', () => {
    const held = holdersOf([
      player(1, 1),
      player(2, 1, { is_archived: true }),
      player(3, null),
      player(4, 2),
    ])
    expect(held.get(1)?.map((p) => p.id)).toEqual([1])
    expect(held.get(2)?.map((p) => p.id)).toEqual([4])
    expect(held.has(3)).toBe(false)
  })

  it('leaves out the player being edited, who cannot clash with themselves', () => {
    expect(holdersOf([player(1, 4)], 1).size).toBe(0)
  })

  it('lists every holder of a shared colour', () => {
    const held = holdersOf([player(1, 1), player(2, 1)])
    expect(held.get(1)?.map((p) => p.id)).toEqual([1, 2])
  })
})

describe('the colour to offer next', () => {
  it('starts at the first when nobody has one', () => {
    expect(suggest([])).toBe(1)
  })

  it('is the lowest free one', () => {
    expect(suggest([player(1, 1), player(2, 3)])).toBe(2)
  })

  it('hands out all eight before repeating', () => {
    const chosen: number[] = []
    const players: Player[] = []
    for (let i = 1; i <= ACCENT_COUNT; i++) {
      const accent = suggest(players)
      chosen.push(accent)
      players.push(player(i, accent))
    }
    expect(chosen.sort((a, b) => a - b)).toEqual([...ACCENTS])
  })

  it('degrades to the least-held once the palette is full', () => {
    // The ninth player has no free colour to be given: eight is the whole
    // palette, and #4 forbids extending it by hand.
    const full = ACCENTS.map((accent) => player(accent, accent))
    expect(suggest(full)).toBe(1)
    expect(suggest([...full, player(9, 1)])).toBe(2)
  })

  it('ignores archived players, who are on no scoreboard', () => {
    expect(suggest([player(1, 1, { is_archived: true })])).toBe(1)
  })
})
