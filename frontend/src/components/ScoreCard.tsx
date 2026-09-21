import type { CSSProperties } from 'react'
import './ScoreCard.css'

export interface ScoreCardProps {
  name: string
  score: number
  /** CSS colour for the identity stripe, normally a player accent token. */
  accent?: string
  active?: boolean
  /** Three-dart average, already rounded by the caller. */
  average?: number
  legs?: number
}

export function ScoreCard({ name, score, accent, active = false, average, legs }: ScoreCardProps) {
  const style =
    accent !== undefined ? ({ '--scorecard-accent': accent } as CSSProperties) : undefined

  return (
    <div className={`scorecard${active ? ' scorecard--active' : ''}`} style={style}>
      <div className="scorecard__name">
        <span className="scorecard__name-text">{name}</span>
        {active && <span className="scorecard__turn">Throwing</span>}
      </div>
      <div className="scorecard__score tnum">{score}</div>
      <div className="scorecard__meta tnum">
        {average !== undefined && <span>Avg {average.toFixed(1)}</span>}
        {legs !== undefined && <span>Legs {legs}</span>}
      </div>
    </div>
  )
}
