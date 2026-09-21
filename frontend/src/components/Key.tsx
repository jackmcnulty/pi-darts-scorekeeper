import type { ButtonHTMLAttributes } from 'react'
import './Key.css'

export type KeyVariant = 'default' | 'accent' | 'danger'

export interface KeyProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  /** Small caption under the label, e.g. the value a triple is worth. */
  sub?: string
  variant?: KeyVariant
  wide?: boolean
  pressed?: boolean
}

export function Key({
  label,
  sub,
  variant = 'default',
  wide = false,
  pressed = false,
  className,
  ...rest
}: KeyProps) {
  const classes = ['key', `key--${variant}`, wide ? 'key--wide' : '', className ?? '']
    .filter(Boolean)
    .join(' ')

  return (
    <button type="button" className={classes} data-pressed={pressed} {...rest}>
      <span>{label}</span>
      {sub !== undefined && <span className="key__sub">{sub}</span>}
    </button>
  )
}
