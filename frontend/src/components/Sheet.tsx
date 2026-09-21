import type { ReactNode } from 'react'
import './Sheet.css'

export interface SheetProps {
  open: boolean
  title: string
  onClose?: () => void
  children: ReactNode
}

/** Bottom sheet. Static here — no drag-to-dismiss until a real screen needs it. */
export function Sheet({ open, title, onClose, children }: SheetProps) {
  if (!open) return null

  return (
    <>
      <div className="sheet__scrim" onClick={onClose} aria-hidden="true" />
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title}>
        <div className="sheet__grabber" />
        <h2 className="sheet__title">{title}</h2>
        <div className="sheet__body">{children}</div>
      </div>
    </>
  )
}
