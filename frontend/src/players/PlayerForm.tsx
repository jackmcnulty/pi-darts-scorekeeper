/**
 * Adding and editing a player, in the one sheet that does both.
 *
 * It holds no reset logic. The screen mounts it fresh for each player -- and
 * unmounts it on close -- so `useState`'s initialisers are the whole story and
 * there is no effect that can leave yesterday's name in the field.
 *
 * The duplicate-name refusal is the interesting failure. The server answers a
 * repeat name with a 409 whose `detail.reason` is `duplicate_name`, and that is
 * what this branches on: not the status, which several different refusals
 * share, and not the message text, which is prose. It lands under the name
 * field rather than in a toast, because it is a fact about that field.
 */
import { useState } from 'react'
import { reasonOf } from '../api/client'
import type { Player, PlayerWrite } from '../api/players'
import { useArchivePlayer, useCreatePlayer, useUpdatePlayer } from '../api/players'
import { Button } from '../components/Button'
import { Sheet } from '../components/Sheet'
import { Toast } from '../components/Toast'
import { AccentPicker } from './AccentPicker'
import { holdersOf, suggest } from './accents'
import './PlayerForm.css'

/** Matches `SHORT_NAME_MAX` in `darts.repo.players`; the server refuses more. */
const SHORT_NAME_MAX = 8

export interface PlayerFormProps {
  /** The player being edited, or `null` when adding a new one. */
  player: Player | null
  /** Everybody, so the picker knows which colours are taken. */
  players: readonly Player[]
  onClose: () => void
}

export function PlayerForm({ player, players, onClose }: PlayerFormProps) {
  const [displayName, setDisplayName] = useState(player?.display_name ?? '')
  const [shortName, setShortName] = useState(player?.short_name ?? '')
  const [accent, setAccent] = useState(player?.accent_index ?? suggest(players, player?.id))
  const [confirmingArchive, setConfirmingArchive] = useState(false)

  const create = useCreatePlayer()
  const update = useUpdatePlayer()
  const archive = useArchivePlayer()

  const failure: unknown = create.error ?? update.error ?? archive.error
  const duplicateName = reasonOf(failure) === 'duplicate_name'
  const pending = create.isPending || update.isPending || archive.isPending
  const trimmed = displayName.trim()

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (trimmed === '' || pending) return
    // Every field is sent on both verbs. On a PATCH that makes the request say
    // exactly what the form shows, rather than relying on the server's rule for
    // fields it left out.
    const body: PlayerWrite = {
      display_name: trimmed,
      short_name: shortName.trim() === '' ? null : shortName.trim(),
      accent_index: accent,
    }
    const done = {
      onSuccess: () => {
        onClose()
      },
    }
    if (player === null) create.mutate(body, done)
    else update.mutate({ id: player.id, body }, done)
  }

  function retype(value: string) {
    setDisplayName(value)
    // The inline error describes the name that was sent, so it stops being true
    // the moment the name changes.
    create.reset()
    update.reset()
  }

  return (
    <Sheet open title={player === null ? 'Add player' : 'Edit player'} onClose={onClose}>
      <form className="player-form" onSubmit={submit}>
        {/* `htmlFor` rather than a wrapping label: a label that wrapped the
            hint and the error would read them out as part of the field's own
            name, and "Short name Optional. Used on the scoreboard..." is not
            what that field is called. */}
        <div className="player-form__field">
          <label className="player-form__label" htmlFor="player-form-name">
            Name
          </label>
          <input
            id="player-form-name"
            className="player-form__input"
            value={displayName}
            onChange={(event) => {
              retype(event.target.value)
            }}
            aria-invalid={duplicateName}
            aria-describedby={duplicateName ? 'player-form-name-error' : undefined}
            autoFocus
          />
          {duplicateName && (
            <span className="player-form__error" id="player-form-name-error" role="alert">
              Somebody is already called that.
            </span>
          )}
        </div>

        <div className="player-form__field">
          <label className="player-form__label" htmlFor="player-form-short">
            Short name
          </label>
          <input
            id="player-form-short"
            className="player-form__input"
            value={shortName}
            maxLength={SHORT_NAME_MAX}
            onChange={(event) => {
              setShortName(event.target.value)
            }}
            aria-describedby="player-form-short-hint"
          />
          <span className="player-form__hint" id="player-form-short-hint">
            Optional. Used on the scoreboard when the full name will not fit.
          </span>
        </div>

        <div className="player-form__field">
          <span className="player-form__label">Colour</span>
          <AccentPicker
            value={accent}
            onChange={setAccent}
            holders={holdersOf(players, player?.id)}
          />
        </div>

        {failure !== null && failure !== undefined && !duplicateName && (
          <Toast message={(failure as Error).message} variant="danger" />
        )}

        <div className="player-form__actions">
          <Button type="submit" block disabled={trimmed === '' || pending}>
            {player === null ? 'Add player' : 'Save'}
          </Button>
          {player !== null &&
            (confirmingArchive ? (
              <Button
                variant="danger"
                block
                disabled={pending}
                onClick={() => {
                  archive.mutate(player.id, {
                    onSuccess: () => {
                      onClose()
                    },
                  })
                }}
              >
                Archive {player.display_name} for good
              </Button>
            ) : (
              <Button
                variant="ghost"
                block
                disabled={pending}
                onClick={() => {
                  setConfirmingArchive(true)
                }}
              >
                Archive
              </Button>
            ))}
          {confirmingArchive && (
            <p className="player-form__hint">
              They leave the pickers and keep every match, dart and statistic.
            </p>
          )}
        </div>
      </form>
    </Sheet>
  )
}
