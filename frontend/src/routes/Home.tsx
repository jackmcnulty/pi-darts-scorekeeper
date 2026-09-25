/**
 * Where the app opens, and the one screen that has to notice an unfinished game.
 *
 * The resume card is absent rather than empty when there is nothing to resume.
 * A placeholder saying "no match in progress" would be a permanent fixture of
 * the common case, and the common case is that the last match finished.
 *
 * It is also absent while the answer is still on its way. The card is a claim
 * that a game is waiting, and a skeleton in its place would make that claim
 * before it is known, then take it back.
 */
import { Link } from 'react-router'
import { useResumableMatch } from '../api/matches'
import { describeMatch, opponents } from '../matches/history'
import './Home.css'

export function Home() {
  const resumable = useResumableMatch()
  const match = resumable.data ?? null

  return (
    <div className="home">
      <h1 className="home__title">Darts</h1>

      {match !== null && (
        // The play screen resolves the leg itself from the match, which is why
        // this is `/play/:matchId` and not a leg URL: `current_leg_id` is a fact
        // that changes while you walk to the board.
        <Link className="home__resume" to={`/play/${match.id}`}>
          <span className="home__resume-eyebrow">Still playing</span>
          <span className="home__resume-title">{describeMatch(match)}</span>
          <span className="home__resume-players">{opponents(match)}</span>
        </Link>
      )}

      <nav className="home__actions" aria-label="Main">
        <Link className="home__action home__action--primary" to="/setup">
          New match
        </Link>
        <Link className="home__action" to="/players">
          Players
        </Link>
        {/* #22 left /history unreachable by tapping -- criterion 5 only asks for
            a deep link, but a list nobody can navigate to is not a screen. */}
        <Link className="home__action" to="/history">
          History
        </Link>
        <Link className="home__action" to="/stats">
          Stats
        </Link>
      </nav>
    </div>
  )
}
