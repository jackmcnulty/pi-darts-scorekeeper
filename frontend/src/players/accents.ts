/**
 * The player accent palette, as the screens see it.
 *
 * #4's `tokens.css` declares eight accents, chosen by a search that maximised
 * the smallest perceptual distance across normal vision and simulated
 * protanopia, deuteranopia and tritanopia. The database stores an index into
 * that list and never a colour, so this module is the only place the two are
 * joined up.
 *
 * `suggest` deliberately repeats a rule the server also implements
 * (`darts.repo.players.next_accent_index`). The server's copy is the one that
 * decides -- it runs inside the write, so two phones adding a player at the
 * same moment cannot be handed the same free colour. This copy only decides
 * which swatch the picker opens on, which has to be answerable without a round
 * trip. If they ever disagree the server wins and the screen redraws.
 */
import type { Player } from '../api/players'

/** Must match `ACCENT_COUNT` in `darts.repo.players`; the CSS is the source of both. */
export const ACCENT_COUNT = 8

export const ACCENTS: readonly number[] = Array.from({ length: ACCENT_COUNT }, (_, i) => i + 1)

/** The CSS colour for an accent, or `undefined` for a player who has none. */
export function accentColour(index: number | null | undefined): string | undefined {
  return index == null ? undefined : `var(--accent-${index})`
}

/**
 * Who holds each accent, counting only the players a scoreboard could show.
 *
 * Archived players are excluded for the same reason the server excludes them:
 * they are not in any picker, so a colour they still hold is not a clash.
 */
export function holdersOf(players: readonly Player[], exclude?: number): Map<number, Player[]> {
  const held = new Map<number, Player[]>()
  for (const player of players) {
    if (player.is_archived || player.accent_index === null || player.id === exclude) continue
    const holders = held.get(player.accent_index)
    if (holders === undefined) held.set(player.accent_index, [player])
    else holders.push(player)
  }
  return held
}

/**
 * The accent to offer next: the lowest-numbered one the fewest players hold.
 *
 * Below nine active players that is always a free colour, which is what keeps
 * two players from sharing one without somebody choosing it. At nine there is
 * no free colour to give -- eight is the whole palette -- so the suggestion
 * degrades to the least-used and the picker says whose colour it is.
 */
export function suggest(players: readonly Player[], exclude?: number): number {
  const held = holdersOf(players, exclude)
  let best = 1
  for (const accent of ACCENTS) {
    if ((held.get(accent)?.length ?? 0) < (held.get(best)?.length ?? 0)) best = accent
  }
  return best
}
