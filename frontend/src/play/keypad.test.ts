/**
 * The keypad's whole contract, enumerated rather than sampled.
 *
 * #24's "all 63 legal throws are reachable" is the same shape of claim as #23's
 * "every reachable configuration posts a valid body", and it gets the same
 * treatment: walk every key against every latch position and check the set of
 * darts that come out, instead of tapping a handful and trusting the pattern.
 *
 * The expected 63 are built here from the board rather than copied from a list,
 * so this agrees with `engine/throws.py`'s `ALL_THROWS` by construction: 20
 * numbers x 3 multipliers, the outer bull, the inner bull, and the miss. The
 * ticket said 62 twice and was corrected; 62 is the count of *scoring* segments
 * and leaves out the miss.
 */
import { describe, expect, it } from 'vitest'
import {
  ABSOLUTE_KEYS,
  ALL_KEYS,
  dartFor,
  dartScore,
  DEFAULT_MULTIPLIER,
  keyLabel,
  keySub,
  MULTIPLIERS,
  NUMBER_KEYS,
  type Dart,
} from './keypad'

/** A dart as a string, so sets and sorting work on it. */
function key(dart: Dart): string {
  return `${String(dart.segment)}x${String(dart.multiplier)}`
}

/** Every legal throw, built from the board the way `ALL_THROWS` is. */
function everyLegalThrow(): Set<string> {
  const throws = new Set<string>()
  for (let segment = 1; segment <= 20; segment += 1) {
    for (const multiplier of [1, 2, 3]) throws.add(key({ segment, multiplier }))
  }
  throws.add(key({ segment: 25, multiplier: 1 })) // 25, the outer bull
  throws.add(key({ segment: 25, multiplier: 2 })) // BULL, which is 25 doubled
  throws.add(key({ segment: 0, multiplier: 0 })) // MISS
  return throws
}

/** Every (key, latch) pair the keypad offers: 23 keys x 3 latch positions. */
function everyCombination(): Dart[] {
  return ALL_KEYS.flatMap((k) => MULTIPLIERS.map((latch) => dartFor(k, latch)))
}

describe('the keypad reaches the whole board', () => {
  it('offers 23 keys: twenty numbers, both bulls and the miss', () => {
    expect(NUMBER_KEYS).toHaveLength(20)
    expect(ABSOLUTE_KEYS).toHaveLength(3)
    expect(ALL_KEYS).toHaveLength(23)
  })

  it('reaches all 63 legal throws, and exactly those', () => {
    const reachable = new Set(everyCombination().map(key))
    const legal = everyLegalThrow()

    expect(legal.size).toBe(63)
    // Sorted arrays rather than set equality so a failure names the throw that
    // is missing or extra instead of just the sizes.
    expect([...reachable].sort()).toEqual([...legal].sort())
  })

  it('never produces a dart the server would refuse', () => {
    for (const dart of everyCombination()) {
      // The two combinations `Throw.__post_init__` raises on and `DartWrite`
      // 422s on: a triple bull, and half a miss.
      expect([dart.segment, dart.multiplier]).not.toEqual([25, 3])
      expect(dart.segment === 0).toBe(dart.multiplier === 0)
    }
  })

  it('needs 69 taps to cover 63 throws, because three keys ignore the latch', () => {
    // Not a redundant restatement of the count: it is the arithmetic behind the
    // decision. 20 numbered keys x 3 latches are 60 distinct throws; the other
    // three keys are absolute, so their nine combinations collapse to three.
    expect(everyCombination()).toHaveLength(69)
    expect(new Set(everyCombination().map(key)).size).toBe(63)
  })
})

describe('the multiplier latch', () => {
  it('starts on single', () => {
    expect(DEFAULT_MULTIPLIER).toBe('single')
  })

  it('multiplies the twenty numbered keys', () => {
    const twenty = NUMBER_KEYS[19]!
    expect(twenty.label).toBe('20')
    expect(dartFor(twenty, 'single')).toEqual({ segment: 20, multiplier: 1 })
    expect(dartFor(twenty, 'double')).toEqual({ segment: 20, multiplier: 2 })
    expect(dartFor(twenty, 'triple')).toEqual({ segment: 20, multiplier: 3 })
  })

  it('is ignored by 25, BULL and MISS whatever it is set to', () => {
    // The decision this test exists for. There is no triple bull, BULL already
    // *is* the double of 25, and a miss has no multiplier -- so these three post
    // the same dart under every latch rather than being disabled under two of
    // them. That is what keeps every throw one tap away.
    const expected: Record<string, Dart> = {
      '25': { segment: 25, multiplier: 1 },
      bull: { segment: 25, multiplier: 2 },
      miss: { segment: 0, multiplier: 0 },
    }
    for (const absolute of ABSOLUTE_KEYS) {
      for (const latch of MULTIPLIERS) {
        expect(dartFor(absolute, latch)).toEqual(expected[absolute.id])
      }
    }
  })
})

describe('what a key says it will do', () => {
  it('scores a dart the way the board does', () => {
    expect(dartScore({ segment: 20, multiplier: 3 })).toBe(60)
    expect(dartScore({ segment: 25, multiplier: 2 })).toBe(50)
    expect(dartScore({ segment: 0, multiplier: 0 })).toBe(0)
  })

  it('captions a numbered key only while the latch is on', () => {
    const twenty = NUMBER_KEYS[19]!
    // On single the key means the number printed on it and needs no caption.
    expect(keySub(twenty, 'single')).toBeUndefined()
    expect(keySub(twenty, 'double')).toBe('40')
    expect(keySub(twenty, 'triple')).toBe('60')
  })

  it('captions the absolute keys with their value, always', () => {
    for (const latch of MULTIPLIERS) {
      expect(keySub(ABSOLUTE_KEYS[0]!, latch)).toBe('25')
      expect(keySub(ABSOLUTE_KEYS[1]!, latch)).toBe('50')
      expect(keySub(ABSOLUTE_KEYS[2]!, latch)).toBe('0')
    }
  })

  it('reads aloud as one phrase rather than two run-together spans', () => {
    const twenty = NUMBER_KEYS[19]!
    expect(keyLabel(twenty, 'single')).toBe('20, single')
    expect(keyLabel(twenty, 'triple')).toBe('20, triple, 60')
    expect(keyLabel(ABSOLUTE_KEYS[1]!, 'triple')).toBe('Bull, 50')
    expect(keyLabel(ABSOLUTE_KEYS[2]!, 'double')).toBe('Miss')
  })

  it('gives every key a distinct id and accessible name', () => {
    expect(new Set(ALL_KEYS.map((k) => k.id)).size).toBe(ALL_KEYS.length)
    for (const latch of MULTIPLIERS) {
      const names = ALL_KEYS.map((k) => keyLabel(k, latch))
      // Two keys sharing a name would make `getByRole('button', { name })`
      // ambiguous, which is how a keypad test starts passing for the wrong key.
      expect(new Set(names).size).toBe(ALL_KEYS.length)
    }
  })

  it('exposes the board number so #25 can dim its non-targets', () => {
    expect(NUMBER_KEYS.map((k) => k.segment)).toEqual(Array.from({ length: 20 }, (_, i) => i + 1))
    // Both bull keys are segment 25, which is a cricket target; the miss is 0,
    // which is not a target and not a number either.
    expect(ABSOLUTE_KEYS.map((k) => k.segment)).toEqual([25, 25, 0])
  })
})
