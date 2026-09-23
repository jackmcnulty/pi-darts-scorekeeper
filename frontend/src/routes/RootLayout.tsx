/**
 * The frame every screen renders inside.
 *
 * Its whole job is to be the one place that knows about the notch. #4 put
 * `viewport-fit=cover` in `index.html` and `--safe-top`/`--safe-bottom`/
 * `--safe-left`/`--safe-right` in `tokens.css`; without something applying
 * them, `viewport-fit=cover` is strictly worse than not setting it, because
 * the app now paints under the Dynamic Island instead of beside it.
 *
 * The connection toast lives here rather than in any screen: a dropped
 * connection is a property of the app, not of whatever route happens to be
 * mounted, and a toast that unmounted on navigation would vanish exactly when
 * somebody tried to navigate away from the problem.
 */
import { Outlet } from 'react-router'
import { ConnectionToast } from '../components/ConnectionToast'
import './RootLayout.css'

export function RootLayout() {
  return (
    <div className="app-shell">
      <main className="app-shell__content">
        <Outlet />
      </main>
      <ConnectionToast />
    </div>
  )
}
