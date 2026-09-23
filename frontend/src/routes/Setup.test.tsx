/**
 * Match setup, driven the way a thumb drives it.
 *
 * The handler records every body posted and answers with a match built from
 * it, so the tests that matter here assert on the wire rather than on the
 * screen: #23's real claim is about what gets sent, and a screen that looks
 * right while posting the wrong teams would pass any number of DOM assertions.
 *
 * The 2v2 test opens at `/` and counts its taps, because "reachable in 6 taps
 * or fewer from the home screen" is a claim about the journey and not about
 * this screen in isolation.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import type { components } from '../api/schema'
import type { MatchWrite } from '../setup/config'
import '../styles/global.css'
import { installServer, renderApp, server } from '../test-harness'

installServer()

type Player = components['schemas']['PlayerResponse']
type Match = components['schemas']['MatchResponse']

function player(id: number, display_name: string): Player {
  return {
    id,
    display_name,
    short_name: null,
    accent_index: id,
    is_archived: false,
    created_at: '2026-09-23T19:00:00.000Z',
  }
}

/** A match the server would have built from this body, id 42. */
function created(body: MatchWrite): Match {
  return {
    id: 42,
    config: body.config,
    status: 'in_progress',
    created_at: '2026-09-23T20:00:00.000Z',
    completed_at: null,
    abandoned_at: null,
    winner_team_id: null,
    current_leg_id: 7,
    teams: body.teams.map((team, team_index) => ({
      id: team_index + 1,
      team_index,
      name: team.name ?? null,
      is_solo: team.player_ids.length === 1,
      members: team.player_ids.map((player_id, member_index) => ({
        player_id,
        member_index,
        display_name: `Player ${player_id}`,
        is_archived: false,
      })),
    })),
  }
}

/** The 422 the server sends when a config is refused, built fresh each time:
 *  a Response body can only be read once, and a shared instance is consumed. */
function refusal() {
  return HttpResponse.json(
    {
      error: {
        code: 'validation_error',
        message: 'best_of must be odd',
        detail: { loc: ['body', 'config', 'best_of'] },
      },
    },
    { status: 422 },
  )
}

let roster: Player[]
/** Every match body the screen sent, in order, for asserting on the wire. */
let posted: MatchWrite[]
/** Whatever `GET /api/matches?status=in_progress` should answer with. */
let inProgress: Match[]

beforeEach(() => {
  roster = [player(1, 'Jack'), player(2, 'Dad'), player(3, 'Ellie'), player(4, 'Sam')]
  posted = []
  inProgress = []
  server.use(
    http.get('*/api/players', () => HttpResponse.json(roster)),
    http.get('*/api/matches', () =>
      HttpResponse.json({ items: inProgress, total: inProgress.length, limit: 1, offset: 0 }),
    ),
    http.post('*/api/matches', async ({ request }) => {
      const body = (await request.json()) as MatchWrite
      posted.push(body)
      return HttpResponse.json(created(body), { status: 201 })
    }),
  )
})

/** Wait for the roster, which is the last thing the screen needs to be usable. */
async function rosterList() {
  return screen.findByRole('list')
}

/** The player row for a name, found by a prefix so its team can change under us. */
function row(name: string) {
  return screen.getByRole('button', { name: new RegExp(`^${name},`) })
}

describe('control visibility', () => {
  it('shows the x01 rule controls for an x01 game', async () => {
    renderApp('/setup')
    await rosterList()

    expect(screen.getByRole('radiogroup', { name: 'In rule' })).toBeInTheDocument()
    expect(screen.getByRole('radiogroup', { name: 'Out rule' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '501' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('hides them when a cricket variant is selected, and brings them back', async () => {
    const user = userEvent.setup()
    renderApp('/setup')
    await rosterList()

    await user.click(screen.getByRole('button', { name: 'Cut-throat cricket' }))

    expect(screen.queryByRole('radiogroup', { name: 'In rule' })).not.toBeInTheDocument()
    expect(screen.queryByRole('radiogroup', { name: 'Out rule' })).not.toBeInTheDocument()
    // The leg count is not an x01 rule and stays: every game is played in legs.
    expect(screen.getByRole('button', { name: 'Increase Legs to win' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '301' }))

    expect(screen.getByRole('radiogroup', { name: 'In rule' })).toBeInTheDocument()
    expect(screen.getByRole('radiogroup', { name: 'Out rule' })).toBeInTheDocument()
  })

  it('offers all three cricket variants and all three x01 scores as one picker', async () => {
    renderApp('/setup')
    await rosterList()

    for (const name of ['301', '501', '701', 'Cricket', 'Cut-throat cricket', 'Quick cricket']) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument()
    }
  })
})

describe('the start button', () => {
  it('is disabled until both teams have a player', async () => {
    const user = userEvent.setup()
    renderApp('/setup')
    await rosterList()

    const start = screen.getByRole('button', { name: 'Start match' })
    expect(start).toBeDisabled()

    await user.click(row('Jack'))
    expect(start).toBeDisabled()

    await user.click(row('Dad'))
    expect(start).toBeEnabled()

    // Moving Dad across leaves Team B empty, and the button goes back.
    await user.click(row('Dad'))
    expect(row('Dad')).toHaveAccessibleName('Dad, Team A')
    expect(start).toBeDisabled()
  })

  it('cannot be pressed twice while the first request is in flight', async () => {
    const user = userEvent.setup()
    // Held open rather than delayed by a timer: the point is that the pending
    // state is observable, and a race against the clock would decide that.
    let release: () => void = () => undefined
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    server.use(
      http.post('*/api/matches', async ({ request }) => {
        const body = (await request.json()) as MatchWrite
        posted.push(body)
        await held
        return HttpResponse.json(created(body), { status: 201 })
      }),
    )
    renderApp('/setup')
    await rosterList()

    await user.click(row('Jack'))
    await user.click(row('Dad'))
    await user.click(screen.getByRole('button', { name: 'Start match' }))

    // A create that is retried is a second match, so the mutation deliberately
    // does not retry — which makes a second tap the only way to get one.
    const starting = await screen.findByRole('button', { name: 'Starting…' })
    expect(starting).toBeDisabled()
    await user.click(starting)

    release()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Play' })).toBeInTheDocument()
    })
    expect(posted).toHaveLength(1)
  })

  it('surfaces a refusal from the server instead of navigating', async () => {
    const user = userEvent.setup()
    server.use(http.post('*/api/matches', () => refusal()))
    renderApp('/setup')
    await rosterList()

    await user.click(row('Jack'))
    await user.click(row('Dad'))
    await user.click(screen.getByRole('button', { name: 'Start match' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('best_of must be odd')
    expect(screen.getByRole('heading', { name: 'New match' })).toBeInTheDocument()
  })
})

describe('building a match', () => {
  it('reaches a 2v2 in six taps from the home screen', async () => {
    const user = userEvent.setup()
    renderApp('/')

    // 1.
    await user.click(await screen.findByRole('link', { name: 'New match' }))
    await rosterList()

    // 2, 3, 4, 5 — the fill alternates, so no pairing taps are needed.
    for (const name of ['Jack', 'Dad', 'Ellie', 'Sam']) {
      await user.click(row(name))
    }

    expect(row('Jack')).toHaveAccessibleName('Jack, Team A')
    expect(row('Dad')).toHaveAccessibleName('Dad, Team B')
    expect(row('Ellie')).toHaveAccessibleName('Ellie, Team A')
    expect(row('Sam')).toHaveAccessibleName('Sam, Team B')

    // 6.
    await user.click(screen.getByRole('button', { name: 'Start match' }))

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Play' })).toBeInTheDocument()
    })
    expect(posted).toEqual([
      {
        config: {
          game_type: 'x01',
          start_score: 501,
          in_rule: 'straight',
          out_rule: 'double',
          best_of: 5,
          start_rule: 'alternate',
          fixed_team: 0,
        },
        teams: [{ player_ids: [1, 3] }, { player_ids: [2, 4] }],
      },
    ])
  })

  it('posts an uneven 2v1', async () => {
    const user = userEvent.setup()
    renderApp('/setup')
    await rosterList()

    for (const name of ['Jack', 'Dad', 'Ellie']) {
      await user.click(row(name))
    }
    await user.click(screen.getByRole('button', { name: 'Start match' }))

    await waitFor(() => {
      expect(posted).toHaveLength(1)
    })
    expect(posted[0]?.teams).toEqual([{ player_ids: [1, 3] }, { player_ids: [2] }])
  })

  it('posts a cricket body with the chosen variant and no x01 rules', async () => {
    const user = userEvent.setup()
    renderApp('/setup')
    await rosterList()

    await user.click(screen.getByRole('button', { name: 'Quick cricket' }))
    await user.click(row('Jack'))
    await user.click(row('Dad'))
    await user.click(screen.getByRole('button', { name: 'Decrease Legs to win' }))
    await user.click(screen.getByRole('button', { name: 'Start match' }))

    await waitFor(() => {
      expect(posted).toHaveLength(1)
    })
    expect(posted[0]?.config).toEqual({
      game_type: 'cricket',
      variant: 'quick',
      best_of: 3,
      start_rule: 'alternate',
      fixed_team: 0,
    })
  })

  it('carries the x01 rule controls into the body', async () => {
    const user = userEvent.setup()
    renderApp('/setup')
    await rosterList()

    await user.click(screen.getByRole('button', { name: '701' }))
    const inRule = screen.getByRole('radiogroup', { name: 'In rule' })
    await user.click(within(inRule).getByRole('radio', { name: 'Double in' }))
    const outRule = screen.getByRole('radiogroup', { name: 'Out rule' })
    await user.click(within(outRule).getByRole('radio', { name: 'Straight out' }))
    await user.click(screen.getByRole('button', { name: 'Increase Legs to win' }))

    await user.click(row('Jack'))
    await user.click(row('Dad'))
    await user.click(screen.getByRole('button', { name: 'Start match' }))

    await waitFor(() => {
      expect(posted).toHaveLength(1)
    })
    expect(posted[0]?.config).toEqual({
      game_type: 'x01',
      start_score: 701,
      in_rule: 'double',
      out_rule: 'straight',
      best_of: 7,
      start_rule: 'alternate',
      fixed_team: 0,
    })
  })

  it('keeps the teams when the game type changes after they are picked', async () => {
    const user = userEvent.setup()
    renderApp('/setup')
    await rosterList()

    for (const name of ['Jack', 'Dad', 'Ellie', 'Sam']) {
      await user.click(row(name))
    }
    await user.click(screen.getByRole('button', { name: 'Cricket' }))

    expect(row('Jack')).toHaveAccessibleName('Jack, Team A')
    expect(row('Dad')).toHaveAccessibleName('Dad, Team B')
    expect(row('Ellie')).toHaveAccessibleName('Ellie, Team A')
    expect(row('Sam')).toHaveAccessibleName('Sam, Team B')

    await user.click(screen.getByRole('button', { name: 'Start match' }))

    await waitFor(() => {
      expect(posted).toHaveLength(1)
    })
    expect(posted[0]?.teams).toEqual([{ player_ids: [1, 3] }, { player_ids: [2, 4] }])
    expect(posted[0]?.config.variant).toBe('standard')
  })

  it('lets a player be moved across and then sat out', async () => {
    const user = userEvent.setup()
    renderApp('/setup')
    await rosterList()

    await user.click(row('Jack'))
    expect(row('Jack')).toHaveAccessibleName('Jack, Team A')

    await user.click(row('Jack'))
    expect(row('Jack')).toHaveAccessibleName('Jack, Team B')

    await user.click(row('Jack'))
    expect(row('Jack')).toHaveAccessibleName('Jack, not playing')
  })
})

describe('a match already in progress', () => {
  it('says so and offers to resume, without blocking a new one', async () => {
    const user = userEvent.setup()
    inProgress = [
      created({
        config: {
          game_type: 'x01',
          start_score: 501,
          in_rule: 'straight',
          out_rule: 'double',
          best_of: 5,
          start_rule: 'alternate',
          fixed_team: 0,
        },
        teams: [{ player_ids: [1] }, { player_ids: [2] }],
      }),
    ]
    renderApp('/setup')
    await rosterList()

    expect(await screen.findByRole('link', { name: 'Resume it instead' })).toHaveAttribute(
      'href',
      '/play/42',
    )

    await user.click(row('Jack'))
    await user.click(row('Dad'))
    expect(screen.getByRole('button', { name: 'Start match' })).toBeEnabled()
  })

  it('says nothing when there is nothing to resume', async () => {
    renderApp('/setup')
    await rosterList()

    expect(screen.queryByRole('link', { name: 'Resume it instead' })).not.toBeInTheDocument()
  })
})

describe('the roster', () => {
  it('points at the player screen when nobody is on it', async () => {
    roster = []
    renderApp('/setup')

    expect(await screen.findByRole('link', { name: 'Add whoever is throwing' })).toHaveAttribute(
      'href',
      '/players',
    )
    expect(screen.getByRole('button', { name: 'Start match' })).toBeDisabled()
  })

  it('offers a retry when the list cannot be read', async () => {
    server.use(http.get('*/api/players', () => HttpResponse.error()))
    renderApp('/setup')

    // The query retries an unreachable server three times with real backoff,
    // so this waits on the outcome and never on the clock.
    const retry = await screen.findByRole('button', { name: 'Try again' }, { timeout: 5000 })

    server.use(http.get('*/api/players', () => HttpResponse.json(roster)))
    await userEvent.setup().click(retry)

    expect(await rosterList()).toBeInTheDocument()
  })
})
