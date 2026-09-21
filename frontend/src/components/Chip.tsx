import type { ButtonHTMLAttributes, HTMLAttributes } from 'react'
import './Chip.css'

interface CommonChipProps {
  label: string
  /** CSS colour for the leading dot, normally a player accent token. */
  accent?: string
  selected?: boolean
}

export interface ChipProps
  extends CommonChipProps, Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  pressed?: boolean
}

export function Chip({ label, accent, selected = false, pressed = false, ...rest }: ChipProps) {
  const classes = ['chip', selected ? 'chip--selected' : ''].filter(Boolean).join(' ')
  return (
    <button
      type="button"
      className={classes}
      aria-pressed={selected}
      data-pressed={pressed}
      {...rest}
    >
      {accent !== undefined && <span className="chip__dot" style={{ background: accent }} />}
      {label}
    </button>
  )
}

export type StaticChipProps = CommonChipProps & Omit<HTMLAttributes<HTMLSpanElement>, 'children'>

/** Non-interactive variant, for labelling rather than choosing. */
export function StaticChip({ label, accent, ...rest }: StaticChipProps) {
  return (
    <span className="chip chip--static" {...rest}>
      {accent !== undefined && <span className="chip__dot" style={{ background: accent }} />}
      {label}
    </span>
  )
}
