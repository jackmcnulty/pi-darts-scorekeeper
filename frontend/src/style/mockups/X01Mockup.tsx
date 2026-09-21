import { Key } from '../../components/Key'
import { ScoreCard } from '../../components/ScoreCard'
import { SegmentedControl } from '../../components/SegmentedControl'
import './mockup.css'
import './X01Mockup.css'

const NUMBERS = Array.from({ length: 20 }, (_, i) => i + 1)

/**
 * Static mockup of the 501 play screen. Nothing here is wired up — the real
 * screen is #24. This exists to prove the layout survives a 402x874 viewport
 * with the Dynamic Island and home indicator taking their bites out of it.
 */
export function X01Mockup() {
  return (
    <div className="mock">
      <div className="mock__topbar">
        <a className="mock__back" href="#components" aria-label="Back to style guide">
          ‹
        </a>
        <span className="mock__context tnum">501 · Leg 2 of 5 · Best of 5</span>
        <span className="mock__topbar-spacer" />
      </div>

      <div className="x01">
        <div className="x01__cards">
          <ScoreCard
            name="Jack"
            score={134}
            accent="var(--accent-2)"
            active
            average={58.4}
            legs={1}
          />
          <ScoreCard name="Dad" score={301} accent="var(--accent-1)" average={41.2} legs={1} />
        </div>

        <div className="x01__turn">
          <div className="x01__darts">
            <span className="x01__dart tnum">T20</span>
            <span className="x01__dart tnum">20</span>
            <span className="x01__dart x01__dart--empty">–</span>
          </div>
          <div className="x01__checkout">
            <span className="x01__checkout-label">Checkout</span>
            <span className="x01__checkout-value tnum">T18 D20</span>
          </div>
        </div>

        <div className="x01__multiplier">
          <SegmentedControl
            label="Dart multiplier"
            value="single"
            options={[
              { value: 'single', label: 'Single' },
              { value: 'double', label: 'Double' },
              { value: 'triple', label: 'Triple' },
            ]}
          />
        </div>

        <div className="x01__grid">
          {NUMBERS.map((n) => (
            <Key key={n} label={String(n)} />
          ))}
        </div>

        <div className="x01__actions mock__footer">
          <Key label="25" />
          <Key label="BULL" sub="50" />
          <Key label="MISS" sub="0" />
          <Key label="UNDO" variant="danger" wide />
        </div>
      </div>
    </div>
  )
}
