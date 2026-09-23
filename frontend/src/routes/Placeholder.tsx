/**
 * A route that exists so the shell can be tested, and nothing more.
 *
 * #21 is explicitly the skeleton: routing, the API client, the PWA layer and
 * this layout. Every screen is #22 onwards, and guessing at their content
 * here would mean #22 spending its first hour deleting someone else's idea of
 * a home screen. So each of these says which ticket owns it and stops.
 *
 * They are real routes, though, not a stub of routing: they mount inside the
 * real layout, at the real paths, which is what makes "reloading /history/42
 * loads that screen" a thing a test can check today.
 */
import './Placeholder.css'

export interface PlaceholderProps {
  title: string
  /** The issue that will replace this, e.g. `#24`. */
  ticket: string
}

export function Placeholder({ title, ticket }: PlaceholderProps) {
  return (
    <div className="placeholder">
      <h1 className="placeholder__title">{title}</h1>
      <p className="placeholder__ticket">Waiting on {ticket}</p>
    </div>
  )
}
