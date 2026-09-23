/**
 * The message that replaces a white screen when the Wi-Fi drops mid-leg.
 *
 * It says what is wrong and what is being done about it, and then it gets out
 * of the way on its own: `ConnectionMonitor` clears the moment any request is
 * answered, so nothing here needs a dismiss button that would only ever hide
 * a problem that is still happening.
 *
 * Deliberately not a modal and not a full-screen takeover. The score on the
 * board behind it is the last thing the server confirmed, which is exactly
 * what a player wants to keep looking at while the connection sorts itself
 * out. #21's scope is "the app remains usable", not "the app stops".
 */
import { useReachable } from '../api/connection'
import { Toast } from './Toast'
import './ConnectionToast.css'

export function ConnectionToast() {
  const reachable = useReachable()
  if (reachable) return null

  return (
    <div className="connection-toast">
      <Toast variant="danger" message="Lost the scoreboard. Retrying…" />
    </div>
  )
}
