/**
 * One match, visit by visit and dart by dart.
 *
 * Criterion 5's deep link and reload: everything here comes from the match id in
 * the URL and two GETs, so `/history/42` typed cold, reloaded, or reached from
 * the list renders the same screen. There is no state handed over from the play
 * screen and nothing cached from a previous visit is required.
 *
 * Criterion 3, and why the bust is said three ways
 * ------------------------------------------------
 * A busted visit has to make clear that its darts "counted toward darts-thrown
 * but scored nothing". A strike-through alone does not say that -- it reads as
 * deletion, as though the darts were taken back, which is exactly the wrong
 * idea and is what an undo would have done instead. So a busted visit carries:
 *
 * * the darts struck through, because they scored nothing;
 * * an explicit `BUST` marker, so the strike is not the only signal and is not
 *   carried by colour;
 * * the dart count stated plainly beside it, unstruck, because that is the part
 *   the strike would otherwise deny.
 *
 * And the leg's darts-thrown total counts them, which is asserted in the tests
 * against the same figure `/state` reports. `is_bust`, `counted` and
 * `caused_bust` are all the server's verdicts; nothing here re-derives a bust.
 *
 * Two requests, not one
 * ---------------------
 * `/darts` carries the visits and `/matches/{id}` carries the config, the teams
 * and the status. They are separate because the dart-grain read is #26's own
 * endpoint and the match header is the resource that already existed; a single
 * fat response would have meant widening one of them to carry the other.
 */
import { Link, useParams } from 'react-router'
import { useMatchDarts } from '../api/history'
import { useMatch } from '../api/matches'
import { Button } from '../components/Button'
import {
  describeMatch,
  legDartsThrown,
  opponents,
  statusLabel,
  visitLines,
} from '../matches/history'
import './MatchDetail.css'

export function MatchDetail() {
  const params = useParams()
  // A path parameter is whatever was typed. `/history/nonsense` reaches this
  // component, and asking the server about match NaN would be a 422 reported as
  // if the Pi had a problem. `Play.tsx` guards the same way.
  const parsed = Number(params.matchId)
  const matchId = Number.isInteger(parsed) && parsed > 0 ? parsed : null

  const match = useMatch(matchId ?? 0)
  const darts = useMatchDarts(matchId ?? 0)

  if (matchId === null) {
    return (
      <div className="detail">
        <p className="detail__note">
          That is not a match. <Link to="/history">Back to the history</Link>
        </p>
      </div>
    )
  }

  if (match.isPending || darts.isPending) {
    return (
      <div className="detail">
        <p className="detail__note">Reading the match&hellip;</p>
      </div>
    )
  }

  const failure = match.error ?? darts.error
  if (failure !== null) {
    return (
      <div className="detail">
        <div className="detail__note" role="alert">
          <p>{failure.message}</p>
          <Button
            variant="secondary"
            onClick={() => {
              void match.refetch()
              void darts.refetch()
            }}
          >
            Try again
          </Button>
        </div>
      </div>
    )
  }

  if (match.data === undefined || darts.data === undefined) return null

  // Players are named once on the match rather than on every visit, so the
  // lookup is built here and handed to `visitLines`.
  const names = new Map(
    match.data.teams.flatMap((team) =>
      team.members.map((member) => [member.player_id, member.display_name] as const),
    ),
  )
  // `GameConfig` is one flat model rather than a discriminated union, so
  // `game_type === 'cricket'` does not narrow anything; the literal is what
  // `visitLines` needs and reading it once here is enough.
  const game = match.data.config.game_type === 'cricket' ? 'cricket' : 'x01'
  const teamNames = new Map(
    match.data.teams.map(
      (team) =>
        [team.id, team.name ?? team.members.map((m) => m.display_name).join(' & ')] as const,
    ),
  )

  return (
    <div className="detail">
      <header className="detail__header">
        <h1 className="detail__title">{describeMatch(match.data)}</h1>
        <p className="detail__players">{opponents(match.data)}</p>
        <p className={`detail__status detail__status--${match.data.status}`}>
          {statusLabel(match.data.status)}
        </p>
      </header>

      {darts.data.legs.length === 0 && <p className="detail__note">No darts were thrown.</p>}

      {darts.data.legs.map((leg) => {
        const lines = visitLines(leg, names, game)
        const thrown = legDartsThrown(leg)
        const winner = leg.winner_team_id === null ? null : teamNames.get(leg.winner_team_id)
        return (
          <section
            className="detail__leg"
            key={leg.leg_id}
            aria-labelledby={`leg-${String(leg.leg_id)}`}
          >
            <h2 className="detail__leg-title" id={`leg-${String(leg.leg_id)}`}>
              <span>Leg {leg.leg_index + 1}</span>
              <span className="detail__leg-meta tnum">
                {winner === undefined || winner === null ? 'Unfinished' : `${winner} won`} ·{' '}
                {thrown} darts
              </span>
            </h2>

            {lines.length === 0 ? (
              <p className="detail__note">No darts in this leg.</p>
            ) : (
              <ol className="detail__visits">
                {lines.map((line) => (
                  <li
                    key={line.visitId}
                    className={`detail__visit${line.isBust ? ' detail__visit--bust' : ''}`}
                    aria-label={line.label}
                  >
                    <span className="detail__visit-player">{line.playerName}</span>

                    {/* Struck through when busted -- see the module docstring for
                        why the strike is never the only signal. */}
                    <span className="detail__visit-darts">
                      {line.darts.map((label, index) => (
                        <span key={index} className="detail__dart tnum">
                          {label}
                        </span>
                      ))}
                    </span>

                    {line.isBust ? (
                      <span className="detail__visit-verdict">
                        <span className="detail__bust">BUST</span>
                        {/* Deliberately outside the struck-through run: this is
                            the clause the strike would otherwise contradict. */}
                        <span className="detail__thrown tnum">
                          {line.dartsThrown} {line.dartsThrown === 1 ? 'dart' : 'darts'}, 0 scored
                        </span>
                      </span>
                    ) : (
                      <span className="detail__visit-verdict">
                        <span className="detail__scored tnum">{line.scored}</span>
                        <span className="detail__left tnum">{line.scoreText}</span>
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </section>
        )
      })}

      <p className="detail__back">
        <Link to="/history">Back to the history</Link>
      </p>
    </div>
  )
}
