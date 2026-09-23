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
import { useResumableMatch, type Match } from '../api/matches'
import './Home.css'

function capitalise(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1)
}

/**
 * "501 · Best of 5" or "Cricket · Cutthroat", the way #4's mockups title a game.
 *
 * `variant` and `start_score` are each nullable because the other game type has
 * no use for them, and a match that arrives without the one it needs is
 * described by what it does have. Nothing here supplies a default 501: the
 * server is authoritative about what was configured, and a plausible-looking
 * guess is worse than a shorter title.
 */
function describe({ config }: Match): string {
  // Both are optional *and* nullable in the schema -- FastAPI writes a field
  // with a default that way -- so absence is flattened once, here.
  const variant = config.variant ?? null
  const startScore = config.start_score ?? null
  if (config.game_type === 'cricket') {
    return variant === null ? 'Cricket' : `Cricket · ${capitalise(variant)}`
  }
  const bestOf = `Best of ${config.best_of}`
  return startScore === null ? bestOf : `${startScore} · ${bestOf}`
}

/** "Jack v Dad", naming teams by their members when they have no name of their own. */
function opponents({ teams }: Match): string {
  return teams
    .map((team) => team.name ?? team.members.map((member) => member.display_name).join(' & '))
    .join(' v ')
}

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
          <span className="home__resume-title">{describe(match)}</span>
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
        <Link className="home__action" to="/stats">
          Stats
        </Link>
      </nav>
    </div>
  )
}
