/**
 * Managing the people who play: list, add, edit, archive.
 *
 * Archived players are hidden by the server, not by this screen. `GET
 * /api/players` excludes them unless asked, which is the same list every picker
 * gets -- so "hidden from the picker" is not a rule a future screen can forget
 * to apply. The toggle here is the one caller that asks for the rest, and it
 * exists because a management screen that cannot show you what it retired is
 * hard to trust.
 */
import { useState, type CSSProperties } from 'react'
import { usePlayers, type Player } from '../api/players'
import { Button } from '../components/Button'
import { SegmentedControl } from '../components/SegmentedControl'
import { accentColour } from '../players/accents'
import { PlayerForm } from '../players/PlayerForm'
import './Players.css'

type Scope = 'active' | 'all'

const SCOPES = [
  { value: 'active', label: 'Active' },
  { value: 'all', label: 'With archived' },
] as const

/** Closed, adding, or editing one particular player. */
type Editing = null | { player: Player | null }

/** How a row reads aloud: everything the row shows, in the order it shows it. */
function rowLabel(player: Player): string {
  return [player.display_name, player.short_name, player.is_archived ? 'archived' : null]
    .filter((part) => part !== null && part !== '')
    .join(', ')
}

export function Players() {
  const [scope, setScope] = useState<Scope>('active')
  const [editing, setEditing] = useState<Editing>(null)
  const players = usePlayers(scope === 'all')

  return (
    <div className="players">
      <header className="players__header">
        <h1 className="players__title">Players</h1>
        <Button
          onClick={() => {
            setEditing({ player: null })
          }}
        >
          Add
        </Button>
      </header>

      <SegmentedControl options={SCOPES} value={scope} onChange={setScope} label="Which players" />

      {players.isPending && <p className="players__note">Reading the player list&hellip;</p>}

      {players.isError && (
        <div className="players__note" role="alert">
          <p>{players.error.message}</p>
          <Button variant="secondary" onClick={() => void players.refetch()}>
            Try again
          </Button>
        </div>
      )}

      {players.isSuccess &&
        (players.data.length === 0 ? (
          <p className="players__note">
            Nobody yet. Add whoever is throwing and they will be here next time.
          </p>
        ) : (
          <ul className="players__list">
            {players.data.map((player) => (
              <li key={player.id}>
                {/* Spelled out rather than left to the spans, which sit
                    against each other in the DOM and would otherwise be read
                    as one word: "BartholomewBart". */}
                <button
                  type="button"
                  className="players__row"
                  aria-label={rowLabel(player)}
                  data-archived={player.is_archived}
                  onClick={() => {
                    setEditing({ player })
                  }}
                >
                  {/* A custom property rather than `background` directly, so a
                      player from before #22 -- who has no colour at all --
                      falls back in CSS instead of here. */}
                  <span
                    className="players__dot"
                    style={
                      { '--player-accent': accentColour(player.accent_index) } as CSSProperties
                    }
                    aria-hidden="true"
                  />
                  <span className="players__name">{player.display_name}</span>
                  {player.short_name !== null && (
                    <span className="players__short">{player.short_name}</span>
                  )}
                  {player.is_archived && <span className="players__archived">Archived</span>}
                </button>
              </li>
            ))}
          </ul>
        ))}

      {editing !== null && players.isSuccess && (
        // Keyed and mounted only while open, so each visit starts from the
        // player it was opened on and no effect has to put it back.
        <PlayerForm
          key={editing.player?.id ?? 'new'}
          player={editing.player}
          players={players.data}
          onClose={() => {
            setEditing(null)
          }}
        />
      )}
    </div>
  )
}
