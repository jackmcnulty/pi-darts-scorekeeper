/**
 * The last thing between a render bug and a white screen.
 *
 * A class component because that is still the only way to catch a render
 * error in React; there is no hook equivalent. React Router has its own
 * `errorElement`, which catches what happens inside a route -- this sits
 * outside the router so that a failure in the router, the layout, or the
 * query provider is caught too.
 *
 * The recovery affordance is two buttons, in order of how much they throw
 * away. "Try again" remounts the subtree, which is enough when the error came
 * from a transient piece of state. "Reload" is the bigger hammer for when it
 * did not. Neither pretends the error did not happen: the message stays on
 * screen, because a player who can quote it is worth more than a tidy screen.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'
import './ErrorBoundary.css'

interface Props {
  children: ReactNode
  /** Notified on every caught error. The default logs; tests pass a spy. */
  onError?: (error: Error, info: ErrorInfo) => void
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    if (this.props.onError) {
      this.props.onError(error, info)
      return
    }
    // There is no error reporting service on a LAN-only box, and there is not
    // going to be one. The Safari console is the whole story, so the component
    // stack goes with it -- that is the part that says *where*.
    console.error('render failed', error, info.componentStack)
  }

  retry = (): void => {
    this.setState({ error: null })
  }

  reload = (): void => {
    window.location.reload()
  }

  render(): ReactNode {
    const { error } = this.state
    if (error === null) return this.props.children

    return (
      <div className="error-boundary" role="alert">
        <div className="error-boundary__panel">
          <h1 className="error-boundary__title">Something broke</h1>
          <p className="error-boundary__body">
            The scoreboard hit a bug. Nothing thrown so far is lost — the server keeps the score,
            not this phone.
          </p>
          <p className="error-boundary__detail">{error.message}</p>
          <div className="error-boundary__actions">
            <button type="button" className="error-boundary__action" onClick={this.retry}>
              Try again
            </button>
            <button
              type="button"
              className="error-boundary__action error-boundary__action--quiet"
              onClick={this.reload}
            >
              Reload
            </button>
          </div>
        </div>
      </div>
    )
  }
}
