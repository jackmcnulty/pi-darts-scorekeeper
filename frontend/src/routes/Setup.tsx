/**
 * Building a match: pick a game, pick the rules, pick who is throwing.
 *
 * Every decision this screen makes lives in `setup/config.ts` as a pure
 * reducer, and the only thing rendered from anything other than that state is
 * the roster itself. The start button is live exactly when `buildMatch`
 * returns a body, so "is this startable" and "what gets posted" are one
 * question answered once.
 *
 * The roster is `usePlayers()` with its default, which is the server's
 * active-only list. Archived players are excluded by `GET /api/players`
 * rather than filtered here, and `create_match` would refuse them anyway.
 *
 * On success it navigates to `/play/:matchId`, which is #24's screen and a
 * placeholder until then. That is the whole handover: this screen never sees
 * a leg.
 */
import { useReducer, type CSSProperties } from 'react'
import { Link, useNavigate } from 'react-router'
import { useCreateMatch, useResumableMatch } from '../api/matches'
import { usePlayers, type Player } from '../api/players'
import { Button } from '../components/Button'
import { Chip } from '../components/Chip'
import { SegmentedControl } from '../components/SegmentedControl'
import { Stepper } from '../components/Stepper'
import { accentColour } from '../players/accents'
import {
  buildMatch,
  GAME_OPTIONS,
  INITIAL_STATE,
  isX01,
  MAX_LEGS,
  MIN_LEGS,
  reduce,
  teamOf,
  type Rule,
  type TeamId,
} from '../setup/config'
import './Setup.css'

const IN_RULES = [
  { value: 'straight', label: 'Straight in' },
  { value: 'double', label: 'Double in' },
] as const satisfies readonly { value: Rule; label: string }[]

const OUT_RULES = [
  { value: 'straight', label: 'Straight out' },
  { value: 'double', label: 'Double out' },
] as const satisfies readonly { value: Rule; label: string }[]

/**
 * How a player row reads aloud, spelled out rather than left to the spans.
 *
 * Adjacent spans are concatenated with no separator, so the row would
 * otherwise announce as "JackTeam A". It names the team rather than the tap
 * that would change it, because what the row *is* outlives what one tap does.
 */
function rowLabel(player: Player, team: TeamId | null): string {
  return team === null
    ? `${player.display_name}, not playing`
    : `${player.display_name}, Team ${team}`
}

export function Setup() {
  const [state, dispatch] = useReducer(reduce, INITIAL_STATE)
  const players = usePlayers()
  const resumable = useResumableMatch()
  const createMatch = useCreateMatch()
  const navigate = useNavigate()

  const payload = buildMatch(state)
  const inProgress = resumable.data ?? null

  // Undefined when there is no match to start, which is the single thing the
  // button consults: `buildMatch` returning `null` *is* #23's "at least 2
  // teams and every team has at least 1 player", so the disabled state and
  // the body posted cannot disagree about whether this is a match.
  const start =
    payload === null
      ? undefined
      : () => {
          createMatch.mutate(payload, {
            onSuccess: (match) => {
              void navigate(`/play/${match.id}`)
            },
          })
        }

  return (
    <div className="setup">
      <header className="setup__header">
        {/* In standalone mode there is no browser chrome and therefore no back
            gesture out of a dead end, so the screen provides its own. */}
        <Link className="setup__back" to="/" aria-label="Back to home">
          ‹
        </Link>
        <h1 className="setup__title">New match</h1>
      </header>

      {inProgress !== null && (
        // A warning, not a block. Nothing on the server stops a second match
        // and two people at a board may genuinely want one; what would be
        // wrong is starting one without saying the first is still open.
        <p className="setup__notice">
          A match is still in progress. <Link to={`/play/${inProgress.id}`}>Resume it instead</Link>
          , or start a new one below.
        </p>
      )}

      <div className="setup__body">
        <section className="setup__section" aria-labelledby="setup-game">
          <span className="setup__legend" id="setup-game">
            Game
          </span>
          {/* One flat picker over all six games rather than a game-type control
              plus a variant control: it is what #23 asks for, and it is what
              keeps 501 free -- the default costs no taps at all. */}
          <div className="setup__chips" role="group" aria-labelledby="setup-game">
            {GAME_OPTIONS.map((option) => (
              <Chip
                key={option.value}
                label={option.label}
                aria-label={option.name}
                selected={state.game === option.value}
                onClick={() => {
                  dispatch({ type: 'game', game: option.value })
                }}
              />
            ))}
          </div>
        </section>

        <section className="setup__section" aria-labelledby="setup-rules">
          <span className="setup__legend" id="setup-rules">
            Rules
          </span>
          {/* Cricket has no in or out rule to choose, so the controls are
              absent rather than disabled: a greyed-out "Double out" beside a
              cricket game implies it could apply, and it cannot. The stepper
              stays -- every game is played over legs. */}
          {isX01(state.game) && (
            <>
              <SegmentedControl
                label="In rule"
                options={IN_RULES}
                value={state.inRule}
                onChange={(rule) => {
                  dispatch({ type: 'inRule', rule })
                }}
              />
              <SegmentedControl
                label="Out rule"
                options={OUT_RULES}
                value={state.outRule}
                onChange={(rule) => {
                  dispatch({ type: 'outRule', rule })
                }}
              />
            </>
          )}
          <Stepper
            label="Legs to win"
            value={state.legsToWin}
            min={MIN_LEGS}
            max={MAX_LEGS}
            onChange={(legs) => {
              dispatch({ type: 'legsToWin', legs })
            }}
          />
        </section>

        <section className="setup__section" aria-labelledby="setup-players">
          <span className="setup__legend" id="setup-players">
            Players
          </span>
          <p className="setup__hint">
            Tap to pick sides — the first two taps make it one against one. Tap again to switch
            teams, once more to sit out.
          </p>

          {players.isPending && <p className="setup__note">Reading the player list&hellip;</p>}

          {players.isError && (
            <div className="setup__note" role="alert">
              <p>{players.error.message}</p>
              <Button variant="secondary" onClick={() => void players.refetch()}>
                Try again
              </Button>
            </div>
          )}

          {players.isSuccess &&
            (players.data.length === 0 ? (
              <p className="setup__note">
                Nobody on the roster yet. <Link to="/players">Add whoever is throwing</Link> and
                they will be here next time.
              </p>
            ) : (
              <ul className="setup__players">
                {players.data.map((player) => {
                  const team = teamOf(state, player.id)
                  return (
                    <li key={player.id}>
                      <button
                        type="button"
                        className="setup__player"
                        aria-label={rowLabel(player, team)}
                        aria-pressed={team !== null}
                        data-team={team ?? 'none'}
                        onClick={() => {
                          dispatch({ type: 'tapPlayer', playerId: player.id })
                        }}
                      >
                        <span
                          className="setup__player-dot"
                          style={
                            {
                              '--player-accent': accentColour(player.accent_index),
                            } as CSSProperties
                          }
                          aria-hidden="true"
                        />
                        <span className="setup__player-name">{player.display_name}</span>
                        {team !== null && <span className="setup__player-team">Team {team}</span>}
                      </button>
                    </li>
                  )
                })}
              </ul>
            ))}
        </section>
      </div>

      <div className="setup__footer">
        {createMatch.isError && (
          <p className="setup__error" role="alert">
            {createMatch.error.message}
          </p>
        )}
        <Button block disabled={start === undefined || createMatch.isPending} onClick={start}>
          {createMatch.isPending ? 'Starting…' : 'Start match'}
        </Button>
      </div>
    </div>
  )
}
