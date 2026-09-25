/**
 * The non-scrolling frame both boards sit in, copied from #4's approved shell.
 *
 * `height: 100dvh; overflow: hidden` is the load-bearing pair and the reason
 * this is a component rather than markup: every state either screen can be in
 * -- loading, refused, the x01 board, the cricket board -- has to sit inside the
 * same fixed height, or the one that overflows is the one nobody tested.
 * Overflow clips visibly here instead of quietly turning into a scroll, which is
 * what makes a layout mistake something you can see rather than something you
 * only feel on a phone at a board.
 *
 * #24 had this as a private function inside `Play.tsx`. #25 needs the identical
 * frame around a different board, so it moved out here rather than being
 * duplicated -- two copies of a height budget is two budgets. The styles stay in
 * `Play.css` with the rest of the screen, and `Play.test.tsx` parses them as
 * text, because jsdom does no layout and cannot check the fit itself.
 */
import { Link } from 'react-router'

export function PlayFrame({ context, children }: { context?: string; children: React.ReactNode }) {
  return (
    <div className="play">
      <div className="play__topbar">
        {/* Standalone mode has no browser chrome, so there is no back gesture
            out of here. #23 added the same link to /setup for the same reason. */}
        <Link className="play__back" to="/" aria-label="Back to home">
          ‹
        </Link>
        <span className="play__context tnum">{context ?? 'Match'}</span>
        <span className="play__topbar-spacer" />
      </div>
      {children}
    </div>
  )
}
