import { Button } from '../../components/Button'
import { Chip } from '../../components/Chip'
import { SegmentedControl } from '../../components/SegmentedControl'
import { Stepper } from '../../components/Stepper'
import './mockup.css'
import './SetupMockup.css'

const PLAYERS = [
  { name: 'Jack', accent: 'var(--accent-2)', team: 'Team A' },
  { name: 'Dad', accent: 'var(--accent-1)', team: 'Team B' },
  { name: 'Ellie', accent: 'var(--accent-3)', team: 'Team A' },
]

/** Static mockup of match setup. Not wired up — the real screen is #23. */
export function SetupMockup() {
  return (
    <div className="mock">
      <div className="mock__topbar">
        <a className="mock__back" href="#components" aria-label="Back to style guide">
          ‹
        </a>
        <span className="mock__context">New match</span>
        <span className="mock__topbar-spacer" />
      </div>

      <div className="setup">
        <div className="setup__section">
          <span className="setup__legend">Game</span>
          <SegmentedControl
            label="Game type"
            value="x01"
            options={[
              { value: 'x01', label: 'x01' },
              { value: 'cricket', label: 'Cricket' },
            ]}
          />
          <div className="setup__chips">
            <Chip label="301" />
            <Chip label="501" selected />
            <Chip label="701" />
          </div>
        </div>

        <div className="setup__section">
          <span className="setup__legend">Rules</span>
          <SegmentedControl
            label="Out rule"
            value="double"
            options={[
              { value: 'straight', label: 'Straight out' },
              { value: 'double', label: 'Double out' },
            ]}
          />
          <Stepper label="Legs to win" value={3} min={1} max={9} />
        </div>

        <div className="setup__section">
          <span className="setup__legend">Players</span>
          <div className="setup__players">
            {PLAYERS.map((player) => (
              <div key={player.name} className="setup__player">
                <span className="setup__player-dot" style={{ background: player.accent }} />
                <span className="setup__player-name">{player.name}</span>
                <span className="setup__player-team">{player.team}</span>
              </div>
            ))}
          </div>
          <Button variant="secondary" block>
            Add player
          </Button>
        </div>
      </div>

      <div className="mock__footer">
        <Button block>Start match</Button>
      </div>
    </div>
  )
}
