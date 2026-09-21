import { Button } from '../components/Button'
import { Chip, StaticChip } from '../components/Chip'
import { Key } from '../components/Key'
import { ScoreCard } from '../components/ScoreCard'
import { SegmentedControl } from '../components/SegmentedControl'
import { Sheet } from '../components/Sheet'
import { Stepper } from '../components/Stepper'
import { Toast } from '../components/Toast'
import './StyleGuide.css'

const SURFACES = [
  ['--color-bg', 'bg'],
  ['--color-surface', 'surface'],
  ['--color-surface-raised', 'raised'],
  ['--color-surface-pressed', 'pressed'],
] as const

const INTENTS = [
  ['--color-primary', 'primary'],
  ['--color-danger', 'danger'],
  ['--color-success', 'success'],
  ['--color-warning', 'warning'],
] as const

const ACCENTS = Array.from({ length: 8 }, (_, i) => `--accent-${i + 1}`)

const TYPE_SCALE = [
  ['--text-score', 'score'],
  ['--text-3xl', '3xl'],
  ['--text-2xl', '2xl'],
  ['--text-xl', 'xl'],
  ['--text-lg', 'lg'],
  ['--text-base', 'base'],
  ['--text-sm', 'sm'],
  ['--text-xs', 'xs'],
] as const

function Swatch({ token, name }: { token: string; name: string }) {
  return (
    <div className="sg__swatch">
      <div className="sg__swatch-chip" style={{ background: `var(${token})` }} />
      <span>{name}</span>
    </div>
  )
}

/**
 * Every component in every state, plus links to the three full-screen mockups.
 * Nothing here fetches or mutates anything; states are driven by props, not
 * interaction, so the page renders identically every time.
 */
export function StyleGuide() {
  return (
    <div className="sg">
      <h1 className="sg__title">Darts design system</h1>
      <p className="sg__lede">
        Dark-first, 56px minimum touch targets, tabular figures on anything that changes.
      </p>

      <section className="sg__section">
        <h2 className="sg__h2">Surfaces</h2>
        <div className="sg__swatches">
          {SURFACES.map(([token, name]) => (
            <Swatch key={token} token={token} name={name} />
          ))}
        </div>

        <h3 className="sg__h3">Intent</h3>
        <div className="sg__swatches">
          {INTENTS.map(([token, name]) => (
            <Swatch key={token} token={token} name={name} />
          ))}
        </div>

        <h3 className="sg__h3">Player accents</h3>
        <div className="sg__swatches">
          {ACCENTS.map((token, i) => (
            <Swatch key={token} token={token} name={`accent-${i + 1}`} />
          ))}
        </div>
      </section>

      <section className="sg__section">
        <h2 className="sg__h2">Type scale</h2>
        {TYPE_SCALE.map(([token, name]) => (
          <div key={token} className="sg__type-row">
            <span className="sg__type-name">{name}</span>
            <span className="sg__type-sample" style={{ fontSize: `var(${token})` }}>
              180
            </span>
          </div>
        ))}
      </section>

      <section className="sg__section">
        <h2 className="sg__h2">Button</h2>
        <div className="sg__row">
          <Button variant="primary">Primary</Button>
          <Button variant="secondary">Secondary</Button>
        </div>
        <div className="sg__row">
          <Button variant="danger">Danger</Button>
          <Button variant="ghost">Ghost</Button>
        </div>
        <h3 className="sg__h3">Pressed</h3>
        <div className="sg__row">
          <Button variant="primary" pressed>
            Primary
          </Button>
          <Button variant="secondary" pressed>
            Secondary
          </Button>
        </div>
        <h3 className="sg__h3">Disabled</h3>
        <div className="sg__row">
          <Button variant="primary" disabled>
            Primary
          </Button>
          <Button variant="secondary" disabled>
            Secondary
          </Button>
        </div>
        <h3 className="sg__h3">Block</h3>
        <Button block>Start match</Button>
      </section>

      <section className="sg__section">
        <h2 className="sg__h2">Key</h2>
        <div className="sg__keys">
          <Key label="20" />
          <Key label="19" />
          <Key label="T20" sub="60" variant="accent" />
          <Key label="20" pressed />
          <Key label="20" disabled />
        </div>
        <h3 className="sg__h3">Wide and danger</h3>
        <div className="sg__keys">
          <Key label="BULL" sub="50" />
          <Key label="MISS" sub="0" />
          <Key label="UNDO" variant="danger" wide />
        </div>
      </section>

      <section className="sg__section">
        <h2 className="sg__h2">Chip</h2>
        <div className="sg__row">
          <Chip label="501" />
          <Chip label="501" selected />
          <Chip label="501" pressed />
          <Chip label="501" disabled />
        </div>
        <h3 className="sg__h3">With accent, and static</h3>
        <div className="sg__row">
          <Chip label="Jack" accent="var(--accent-2)" selected />
          <StaticChip label="Double out" />
          <StaticChip label="Team A" accent="var(--accent-3)" />
        </div>
      </section>

      <section className="sg__section">
        <h2 className="sg__h2">SegmentedControl</h2>
        <div className="sg__stack">
          <SegmentedControl
            label="Dart multiplier"
            value="single"
            options={[
              { value: 'single', label: 'Single' },
              { value: 'double', label: 'Double' },
              { value: 'treble', label: 'Treble' },
            ]}
          />
          <SegmentedControl
            label="Game type with a disabled option"
            value="cricket"
            options={[
              { value: 'x01', label: 'x01' },
              { value: 'cricket', label: 'Cricket' },
              { value: 'around', label: 'Around the clock', disabled: true },
            ]}
          />
        </div>
      </section>

      <section className="sg__section">
        <h2 className="sg__h2">Stepper</h2>
        <div className="sg__stack">
          <Stepper label="Legs to win" value={3} min={1} max={9} />
          <Stepper label="At minimum" value={1} min={1} max={9} />
          <Stepper label="At maximum" value={9} min={1} max={9} />
          <Stepper label="Disabled" value={5} disabled />
        </div>
      </section>

      <section className="sg__section">
        <h2 className="sg__h2">ScoreCard</h2>
        <div className="sg__stack">
          <ScoreCard
            name="Jack"
            score={134}
            accent="var(--accent-2)"
            active
            average={58.4}
            legs={1}
          />
          <ScoreCard name="Dad" score={301} accent="var(--accent-1)" average={41.2} legs={1} />
          <ScoreCard name="A player with a very long name" score={2} accent="var(--accent-7)" />
        </div>
      </section>

      <section className="sg__section">
        <h2 className="sg__h2">Toast</h2>
        <div className="sg__stack">
          <Toast message="Match saved" variant="success" />
          <Toast message="Dad is on a checkout" variant="info" />
          <Toast message="Bust — score stands at 134" variant="danger" />
        </div>
      </section>

      <section className="sg__section">
        <h2 className="sg__h2">Sheet</h2>
        <p className="sg__lede">
          Shown open, in a frame. On a real screen it is fixed to the bottom of the viewport.
        </p>
        <div className="sg__sheet-demo">
          <Sheet open title="Leg complete">
            <div className="sg__stack">
              <ScoreCard name="Jack" score={0} accent="var(--accent-2)" average={61.7} legs={2} />
              <Button block>Next leg</Button>
              <Button variant="secondary" block>
                End match
              </Button>
            </div>
          </Sheet>
        </div>
      </section>

      <section className="sg__section">
        <h2 className="sg__h2">Screen mockups</h2>
        <div className="sg__mocklinks">
          <a className="sg__mocklink" href="#x01">
            x01 play screen <span className="sg__mocklink-hint">must not scroll</span>
          </a>
          <a className="sg__mocklink" href="#cricket">
            Cricket board <span className="sg__mocklink-hint">must not scroll</span>
          </a>
          <a className="sg__mocklink" href="#setup">
            Match setup <span className="sg__mocklink-hint">scrolls</span>
          </a>
        </div>
      </section>
    </div>
  )
}
