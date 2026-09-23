/**
 * A client route that does not exist.
 *
 * Reachable in one way only: the server hands `index.html` to every non-`/api`
 * path it cannot find, so a typo'd or stale URL arrives here rather than as a
 * 404 from the Pi. That is the SPA fallback working as designed, and this is
 * the screen that admits it instead of showing a blank layout.
 */
import { Link } from 'react-router'
import './Placeholder.css'

export function NotFound() {
  return (
    <div className="placeholder">
      <h1 className="placeholder__title">No such screen</h1>
      <p className="placeholder__ticket">
        <Link to="/">Back to the scoreboard</Link>
      </p>
    </div>
  )
}
