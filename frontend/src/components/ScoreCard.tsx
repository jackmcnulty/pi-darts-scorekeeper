import type { CSSProperties } from 'react'
import './ScoreCard.css'

export interface ScoreCardProps {
  name: string
  /**
   * x01 remaining. Null renders an em dash, which is what a cricket team has
   * instead of a score -- `TeamLegResponse.remaining` is null all through a
   * cricket leg, so #25 can use this card without inventing a number.
   */
  score: number | null
  /** The rest of the team, under the name. For a 2v2; absent for a solo team. */
  subtitle?: string
  /** CSS colour for the identity stripe, normally a player accent token. */
  accent?: string
  active?: boolean
  /**
   * Three-dart average, already rounded by the caller.
   *
   * Null means the team has not thrown yet, and renders as nothing rather than
   * as 0.0 -- which would read as a bad average instead of an absent one. That
   * is the rule the whole stats layer follows, and the server sends null for
   * exactly this case.
   */
  average?: number | null
  legs?: number
  /**
   * How the card reads aloud, spelled out by the caller.
   *
   * The name, score and meta are stacked spans, which screen readers
   * concatenate with no separator -- "Jack134Legs 1". Without this the card
   * announces as a run-on number.
   */
  label?: string
}

export function ScoreCard({
  name,
  score,
  subtitle,
  accent,
  active = false,
  average,
  legs,
  label,
}: ScoreCardProps) {
  const style =
    accent !== undefined ? ({ '--scorecard-accent': accent } as CSSProperties) : undefined

  return (
    <div
      className={`scorecard${active ? ' scorecard--active' : ''}`}
      style={style}
      role={label === undefined ? undefined : 'group'}
      aria-label={label}
    >
      <div className="scorecard__name">
        <span className="scorecard__name-text">{name}</span>
        {active && <span className="scorecard__turn">Throwing</span>}
      </div>
      <div className="scorecard__score tnum">{score ?? '—'}</div>
      <div className="scorecard__meta tnum">
        {subtitle !== undefined && <span className="scorecard__with">{subtitle}</span>}
        {average != null && <span>Avg {average.toFixed(1)}</span>}
        {legs !== undefined && <span>Legs {legs}</span>}
      </div>
    </div>
  )
}
