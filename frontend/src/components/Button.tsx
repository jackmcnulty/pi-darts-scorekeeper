import type { ButtonHTMLAttributes, ReactNode } from 'react'
import './Button.css'

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  block?: boolean
  /** Forces the pressed appearance. For the style guide; real presses use :active. */
  pressed?: boolean
  children: ReactNode
}

export function Button({
  variant = 'primary',
  block = false,
  pressed = false,
  children,
  className,
  ...rest
}: ButtonProps) {
  const classes = ['btn', `btn--${variant}`, block ? 'btn--block' : '', className ?? '']
    .filter(Boolean)
    .join(' ')

  return (
    <button type="button" className={classes} data-pressed={pressed} {...rest}>
      {children}
    </button>
  )
}
