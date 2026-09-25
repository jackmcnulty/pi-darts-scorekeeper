/**
 * What the keypad is, and what each tap means. Shared with #25.
 *
 * This is the whole vocabulary of the board as a client enters it: 23 keys and
 * a multiplier latch, which between them reach every legal throw. It is a `.ts`
 * with no React in it so that `keypad.test.ts` can enumerate all 63 rather than
 * click a sample of them, and so #25's cricket board inherits the mapping
 * instead of writing a second one.
 *
 * Sixty-three, not sixty-two
 * --------------------------
 * #24 said 62 twice and was corrected while this was built. `engine/throws.py`
 * defines `ALL_THROWS` as 63 and its closing comment exists to pre-empt exactly
 * that off-by-one: 62 counts the board's *scoring* segments, which excludes the
 * miss. A miss is a legal thing for a dart to do, `DartWrite` accepts `(0, 0)`,
 * and #4's mockup gave it a key. 20 numbers x 3 multipliers + 25 + BULL + MISS.
 *
 * The latch is ignored by three keys
 * ----------------------------------
 * The latch covers the 20 numbered keys and nothing else, because the other
 * three have no sensible reading under it. There is no triple bull -- `Throw`
 * raises and `DartWrite` 422s on `(25, 3)` -- BULL already *is* the double of
 * 25, so "double 25" would be a second way to spell a key that exists, and a
 * miss has no multiplier at all. Disabling them while latched was the
 * alternative; it costs a tap and hides a third of the board behind a mode. So
 * they are absolute: they post the same dart whatever the latch says, and the
 * latch still resets afterwards. That is what keeps all 63 throws one tap away
 * and makes an unsendable combination unreachable rather than merely refused.
 */
import type { components } from '../api/schema'

/** The body of one dart, minus its id. Generated, not written. */
export type DartWrite = components['schemas']['DartWrite']

/** A board position: what `DartWrite` asks for, before an id is minted. */
export type Dart = Pick<DartWrite, 'segment' | 'multiplier'>

/**
 * The latch, as a union rather than an enum -- `erasableSyntaxOnly` is set, so
 * there are no enums in this codebase.
 */
export const MULTIPLIERS = ['single', 'double', 'triple'] as const

export type Multiplier = (typeof MULTIPLIERS)[number]

/** Where the latch sits when the screen opens, and after every dart. */
export const DEFAULT_MULTIPLIER: Multiplier = 'single'

/** "Triple", never "treble" -- the house word, including on the control. */
export const MULTIPLIER_LABELS: Record<Multiplier, string> = {
  single: 'Single',
  double: 'Double',
  triple: 'Triple',
}

const MULTIPLIER_VALUE: Record<Multiplier, number> = { single: 1, double: 2, triple: 3 }

export interface KeypadKey {
  /** Stable across renders and used as the React key and the test handle. */
  readonly id: string
  readonly label: string
  /** Small caption under the label. Absolute keys state what they are worth. */
  readonly sub?: string
  /**
   * The dart this key always posts, or null for the 20 numbered keys, whose
   * dart the latch decides. Non-null is exactly "the latch does not apply".
   */
  readonly fixed: Dart | null
  /**
   * The board number behind the key: 1..20, 25 for both bull keys, 0 for the
   * miss. #25 dims the numbers that are not cricket targets, and this is what
   * it reads to know which those are -- without needing to parse `label`.
   */
  readonly segment: number
}

/** 1 through 20, in reading order: the 5x4 grid #4's mockup lays out. */
export const NUMBER_KEYS: readonly KeypadKey[] = Array.from({ length: 20 }, (_, i) => ({
  id: String(i + 1),
  label: String(i + 1),
  fixed: null,
  segment: i + 1,
}))

/** The three the latch does not apply to; see the module docstring. */
export const ABSOLUTE_KEYS: readonly KeypadKey[] = [
  { id: '25', label: '25', sub: '25', fixed: { segment: 25, multiplier: 1 }, segment: 25 },
  { id: 'bull', label: 'BULL', sub: '50', fixed: { segment: 25, multiplier: 2 }, segment: 25 },
  { id: 'miss', label: 'MISS', sub: '0', fixed: { segment: 0, multiplier: 0 }, segment: 0 },
]

export const ALL_KEYS: readonly KeypadKey[] = [...NUMBER_KEYS, ...ABSOLUTE_KEYS]

/**
 * The dart a tap means. The single source of truth for what gets posted.
 *
 * Every caller goes through this -- the request body, the key's caption and
 * the tests all read the same function -- so the keypad cannot label a key as
 * one throw and send another.
 */
export function dartFor(key: KeypadKey, latch: Multiplier): Dart {
  return key.fixed ?? { segment: key.segment, multiplier: MULTIPLIER_VALUE[latch] }
}

/**
 * What a dart is worth, for the caption under a latched numbered key.
 *
 * A miss is worth nothing and both bulls are worth their face value doubled or
 * not, which `segment * multiplier` already gets right.
 */
export function dartScore(dart: Dart): number {
  return dart.segment * dart.multiplier
}

/**
 * The caption to print under a key, or undefined for none.
 *
 * Absolute keys always state their value. A numbered key states it only while
 * the latch is on, where the whole point is that the key no longer means the
 * number printed on it: with Triple latched, "20" sends 60, and saying so under
 * the key is the cheapest guard there is against the mis-entry the
 * reset-after-every-dart rule exists to prevent.
 */
export function keySub(key: KeypadKey, latch: Multiplier): string | undefined {
  if (key.fixed !== null) return key.sub
  if (latch === DEFAULT_MULTIPLIER) return undefined
  return String(dartScore(dartFor(key, latch)))
}

/**
 * How a key reads aloud under the current latch.
 *
 * Spelled out because the label and the caption are adjacent spans, which
 * screen readers concatenate with no separator -- "2060" for a latched 20.
 * `Players.tsx` and `Setup.tsx` build explicit labels for the same reason.
 */
export function keyLabel(key: KeypadKey, latch: Multiplier): string {
  if (key.id === 'miss') return 'Miss'
  if (key.id === 'bull') return 'Bull, 50'
  if (key.id === '25') return 'Outer bull, 25'
  if (latch === DEFAULT_MULTIPLIER) return `${key.label}, single`
  return `${key.label}, ${MULTIPLIER_LABELS[latch].toLowerCase()}, ${String(
    dartScore(dartFor(key, latch)),
  )}`
}
