/**
 * The ‹ that leads one level up, on every screen that is not home.
 *
 * Installed to the home screen there is no browser chrome and no back gesture,
 * so a screen without its own way out is a dead end (#70, found in #32's device
 * pass). #23 and #24 had each hand-copied a ‹ link onto Setup and the play
 * frame, and the two copies had already drifted -- one a fixed 56px box with a
 * small grey glyph, the other a minimum 56px box with a large one. #70 asked to
 * reuse "the existing component", which did not exist; Jack chose to extract
 * one, taking Setup's look. A floor kept in one stylesheet is a floor nobody can
 * forget on the seventh screen.
 *
 * Always a link, never `navigate(-1)`. "Up" is a fixed address per screen
 * (detail → list → home), which is what makes "home in at most two taps" true
 * however the screen was reached -- a deep link, a reload, or a shared URL has
 * no history to go back through.
 *
 * The label is required because the glyph says nothing aloud; it names where
 * the link goes ("Back to history"), not merely that it goes back. The
 * screen's own gutter is the screen's, so a `className` carries the optical
 * pull into it; the box and the floor stay here.
 */
import { Link, type To } from 'react-router'
import './BackLink.css'

export interface BackLinkProps {
  to: To
  /** What a screen reader says: where the link leads. */
  label: string
  className?: string
}

export function BackLink({ to, label, className }: BackLinkProps) {
  return (
    <Link className={['back-link', className].filter(Boolean).join(' ')} to={to} aria-label={label}>
      ‹
    </Link>
  )
}
