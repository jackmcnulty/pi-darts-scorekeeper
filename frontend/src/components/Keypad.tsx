/**
 * The dart entry keypad: 20 numbers, the two bulls, the miss, and undo.
 *
 * A component with its own props rather than markup inside the x01 screen,
 * because #25's scope is "the cricket board, reusing the keypad from #24" --
 * so the thing it reuses has to be a thing. `dimmed` is the hook it needs: in
 * cricket the numbers outside 15-20 and 25 are shown dim but stay enterable,
 * since a dart that lands on 7 still happened.
 *
 * The latch is controlled from outside. #24 resets it to Single after every
 * dart, and only after a dart the screen actually sent -- a tap dropped because
 * one was already in flight must leave the latch alone, or a suppressed tap
 * would silently change what the next one means.
 *
 * `disabled` does not grey the keys. It is held for the few milliseconds a dart
 * is in flight on the LAN, and flashing 23 keys on every throw would be worse
 * than the double tap it guards against; the taps are simply ignored. The
 * finished states that really do stop play -- a won match, an abandoned one --
 * are said in words by the screen above.
 */
import { Key } from './Key'
import { SegmentedControl } from './SegmentedControl'
import {
  ABSOLUTE_KEYS,
  keyLabel,
  keySub,
  MULTIPLIER_LABELS,
  MULTIPLIERS,
  NUMBER_KEYS,
  type KeypadKey,
  type Multiplier,
} from '../play/keypad'
import './Keypad.css'

const MULTIPLIER_OPTIONS = MULTIPLIERS.map((value) => ({
  value,
  label: MULTIPLIER_LABELS[value],
}))

export interface KeypadProps {
  latch: Multiplier
  onLatchChange: (latch: Multiplier) => void
  /** A key was tapped. The caller turns it into a dart via `dartFor`. */
  onKey: (key: KeypadKey) => void
  onUndo: () => void
  /** Board numbers to render dim but still enterable. #25's non-targets. */
  dimmed?: ReadonlySet<number>
  /**
   * Ignore taps on the throw keys: a dart is in flight, or this match is not
   * accepting any. See the module docstring for why this does not grey them.
   */
  disabled?: boolean
  /** Undo is genuinely unavailable -- nothing thrown, or nothing undoable. */
  undoDisabled?: boolean
}

export function Keypad({
  latch,
  onLatchChange,
  onKey,
  onUndo,
  dimmed,
  disabled = false,
  undoDisabled = false,
}: KeypadProps) {
  const tap = (key: KeypadKey) => () => {
    if (disabled) return
    onKey(key)
  }

  return (
    <>
      <div className="keypad__multiplier">
        <SegmentedControl
          label="Dart multiplier"
          value={latch}
          options={MULTIPLIER_OPTIONS}
          onChange={onLatchChange}
        />
      </div>

      <div className="keypad__grid">
        {NUMBER_KEYS.map((key) => (
          <Key
            key={key.id}
            label={key.label}
            sub={keySub(key, latch)}
            aria-label={keyLabel(key, latch)}
            data-dimmed={dimmed?.has(key.segment) ?? false}
            onClick={tap(key)}
          />
        ))}
      </div>

      <div className="keypad__actions">
        {ABSOLUTE_KEYS.map((key) => (
          <Key
            key={key.id}
            label={key.label}
            sub={keySub(key, latch)}
            aria-label={keyLabel(key, latch)}
            data-dimmed={dimmed?.has(key.segment) ?? false}
            onClick={tap(key)}
          />
        ))}
        {/* Not gated on `disabled`: darts are refused once a match is won, but
            undoing the dart that won it is exactly how a misclick gets fixed --
            `play.undo` reopens the leg and unwins the match on purpose. The
            caller says separately when there is nothing to undo, and a disabled
            button cannot be clicked, so no guard is needed here. */}
        <Key label="UNDO" variant="danger" wide disabled={undoDisabled} onClick={onUndo} />
      </div>
    </>
  )
}
