import { Key } from '../../components/Key'
import { SegmentedControl } from '../../components/SegmentedControl'
import './mockup.css'
import './CricketMockup.css'

interface Row {
  target: string
  /** Marks held, 0-3. Three is closed. */
  left: number
  right: number
}

const ROWS: readonly Row[] = [
  { target: '20', left: 3, right: 1 },
  { target: '19', left: 2, right: 3 },
  { target: '18', left: 3, right: 3 },
  { target: '17', left: 1, right: 0 },
  { target: '16', left: 0, right: 2 },
  { target: '15', left: 0, right: 0 },
  { target: 'B', left: 2, right: 1 },
]

/* Cricket notation: one mark is a slash, two is a cross, three closes the number
 * and is drawn as a ringed cross. A plain "✕" for closed reads as the same glyph
 * as the two-mark "X" at arm's length, which is the whole thing you need to tell
 * apart at a glance. */
const MARK_GLYPH = ['·', '/', 'X', '⊗'] as const

function Marks({ count }: { count: number }) {
  const closed = count >= 3
  const classes = [
    'cricket__marks',
    closed ? 'cricket__marks--closed' : '',
    count === 0 ? 'cricket__marks--empty' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={classes} aria-label={closed ? 'Closed' : `${count} marks`}>
      {MARK_GLYPH[Math.min(count, 3)]}
    </div>
  )
}

function PlayerHead({
  name,
  score,
  accent,
  active,
}: {
  name: string
  score: number
  accent: string
  active?: boolean
}) {
  return (
    <div className={`cricket__head${active === true ? ' cricket__head--active' : ''}`}>
      <span className="cricket__head-name">
        <span className="cricket__dot" style={{ background: accent }} />
        {name}
      </span>
      <span className="cricket__head-score tnum">{score}</span>
    </div>
  )
}

/**
 * Static mockup of the American Cricket board. Not wired up — the real screen
 * is #25. Shows the standard variant; cut-throat and quick reuse this board.
 */
export function CricketMockup() {
  return (
    <div className="mock">
      <div className="mock__topbar">
        <a className="mock__back" href="#components" aria-label="Back to style guide">
          ‹
        </a>
        <span className="mock__context tnum">Cricket · Standard · Leg 1</span>
        <span className="mock__topbar-spacer" />
      </div>

      <div className="cricket">
        <div className="cricket__board">
          <div className="cricket__header">
            {/* accent-1 and accent-2 are the first two handed out, and the
                palette is ordered so those are the furthest apart. */}
            <PlayerHead name="Jack" score={41} accent="var(--accent-1)" active />
            <div className="cricket__target">—</div>
            <PlayerHead name="Dad" score={27} accent="var(--accent-2)" />
          </div>

          {ROWS.map((row) => (
            <div key={row.target} className="cricket__row">
              <Marks count={row.left} />
              <div className="cricket__target tnum">{row.target}</div>
              <Marks count={row.right} />
            </div>
          ))}
        </div>

        <SegmentedControl
          label="Dart multiplier"
          value="single"
          options={[
            { value: 'single', label: 'Single' },
            { value: 'double', label: 'Double' },
            { value: 'triple', label: 'Triple' },
          ]}
        />

        <div className="cricket__keys mock__footer">
          <Key label="20" />
          <Key label="19" />
          <Key label="18" />
          <Key label="17" />
          <Key label="16" />
          <Key label="15" />
          {/* BULL is the inner bull and worth 50, as `X01Mockup` and the style
              guide both have it. The real board needs a second key beside it:
              in cricket the outer bull is one mark and the inner is two, which
              one key cannot tell apart -- see #25 and `play/keypad.ts`. */}
          <Key label="BULL" sub="50" />
          <Key label="MISS" sub="0" />
        </div>
      </div>
    </div>
  )
}
