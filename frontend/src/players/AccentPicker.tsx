/**
 * Eight swatches, and the truth about which are already spoken for.
 *
 * A taken colour is shown as taken and can still be chosen. Refusing would mean
 * a ninth player cannot be added at all, and silently allowing it would mean two
 * identical dots on a scoreboard with nothing to explain them. So the picker
 * marks the clash, names who it is with, and lets the choice be made on purpose.
 */
import type { CSSProperties } from 'react'
import type { Player } from '../api/players'
import { ACCENTS, accentColour } from './accents'
import './AccentPicker.css'

export interface AccentPickerProps {
  value: number
  onChange: (accent: number) => void
  /** Who already holds each accent, from `holdersOf`. */
  holders: Map<number, Player[]>
}

function nameList(players: readonly Player[]): string {
  const names = players.map((player) => player.display_name)
  if (names.length <= 1) return names.join('')
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

export function AccentPicker({ value, onChange, holders }: AccentPickerProps) {
  const clash = holders.get(value) ?? []

  return (
    <div className="accent-picker">
      <div className="accent-picker__swatches" role="radiogroup" aria-label="Colour">
        {ACCENTS.map((accent) => {
          const taken = holders.get(accent) ?? []
          const label =
            taken.length > 0 ? `Colour ${accent}, used by ${nameList(taken)}` : `Colour ${accent}`
          return (
            <button
              key={accent}
              type="button"
              role="radio"
              aria-checked={accent === value}
              aria-label={label}
              data-taken={taken.length > 0}
              className={`accent-picker__swatch${accent === value ? ' accent-picker__swatch--selected' : ''}`}
              style={{ '--swatch': accentColour(accent) } as CSSProperties}
              onClick={() => onChange(accent)}
            />
          )
        })}
      </div>
      {clash.length > 0 && (
        <p className="accent-picker__clash" role="status">
          {`Already ${nameList(clash)}’s colour.`}
        </p>
      )}
    </div>
  )
}
