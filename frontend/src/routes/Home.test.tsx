/**
 * The home screen, and the resume card that is either right or absent.
 *
 * Every test answers `GET /api/matches` the way the real endpoint does, through
 * MSW, so what is being checked is the screen's reading of a real payload
 * shape rather than of a convenient one.
 */
import { cleanup, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import type { components } from '../api/schema'
// `main.tsx` is what imports this in the app, and it is not in the tree here.
// Without it `--touch-min` is undeclared and the floor assertion is vacuous.
import '../styles/global.css'
import { installServer, renderApp, server } from '../test-harness'

installServer()

type Match = components['schemas']['MatchResponse']

function team(id: number, index: number, name: string): Match['teams'][number] {
  return {
    id,
    team_index: index,
    name: null,
    is_solo: true,
    members: [{ player_id: id, member_index: 0, display_name: name, is_archived: false }],
  }
}

function inProgress(overrides: Partial<Match> = {}): Match {
  return {
    id: 42,
    config: {
      game_type: 'x01',
      best_of: 5,
      fixed_team: 0,
      start_rule: 'alternate',
      start_score: 501,
      in_rule: 'straight',
      out_rule: 'double',
      variant: null,
    },
    status: 'in_progress',
    created_at: '2026-09-23T19:00:00.000Z',
    completed_at: null,
    abandoned_at: null,
    winner_team_id: null,
    current_leg_id: 7,
    teams: [team(1, 0, 'Jack'), team(2, 1, 'Dad')],
    ...overrides,
  }
}

/** What `GET /api/matches?status=in_progress&limit=1` returns for `items`. */
function servesMatches(items: Match[]) {
  server.use(
    http.get('*/api/matches', ({ request }) => {
      const query = new URL(request.url).searchParams
      expect(query.get('status')).toBe('in_progress')
      expect(query.get('limit')).toBe('1')
      return HttpResponse.json({ items, total: items.length, limit: 1, offset: 0 })
    }),
  )
}

describe('the resume card', () => {
  it('names the match in progress and deep-links into the play screen', async () => {
    servesMatches([inProgress()])
    renderApp('/')

    const resume = await screen.findByRole('link', { name: /still playing/i })
    expect(resume).toHaveAttribute('href', '/play/42')
    expect(resume).toHaveTextContent('501 · Best of 5')
    expect(resume).toHaveTextContent('Jack v Dad')
  })

  it('describes a cricket match by its variant', async () => {
    servesMatches([
      inProgress({
        config: {
          game_type: 'cricket',
          best_of: 3,
          fixed_team: 0,
          start_rule: 'alternate',
          start_score: null,
          in_rule: null,
          out_rule: null,
          variant: 'cutthroat',
        },
      }),
    ])
    renderApp('/')

    expect(await screen.findByText('Cricket · Cutthroat')).toBeInTheDocument()
  })

  it('describes a match by what it has, rather than guessing what it lacks', async () => {
    // Both halves of a config are nullable because the other game type has no
    // use for them. Neither absence is invented around.
    servesMatches([
      inProgress({
        config: {
          game_type: 'cricket',
          best_of: 3,
          fixed_team: 0,
          start_rule: 'alternate',
          start_score: null,
          in_rule: null,
          out_rule: null,
          variant: null,
        },
      }),
    ])
    renderApp('/')
    expect(await screen.findByText('Cricket')).toBeInTheDocument()

    cleanup()
    servesMatches([
      inProgress({
        config: {
          game_type: 'x01',
          best_of: 3,
          fixed_team: 0,
          start_rule: 'alternate',
          start_score: null,
          in_rule: 'straight',
          out_rule: 'double',
          variant: null,
        },
      }),
    ])
    renderApp('/')
    expect(await screen.findByText('Best of 3')).toBeInTheDocument()
  })

  it('names a team by its own name when it has one', async () => {
    const doubles = inProgress({
      teams: [{ ...team(1, 0, 'Jack'), name: 'The Arrows', is_solo: false }, team(2, 1, 'Dad')],
    })
    servesMatches([doubles])
    renderApp('/')

    expect(await screen.findByText('The Arrows v Dad')).toBeInTheDocument()
  })

  it('is absent, not empty, when nothing is in progress', async () => {
    servesMatches([])
    renderApp('/')

    // The rest of the screen has arrived, so this is the settled state and not
    // a card that simply has not loaded yet.
    expect(await screen.findByRole('link', { name: 'New match' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /still playing/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/no match/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/resume/i)).not.toBeInTheDocument()
  })

  it('stays absent when the Pi refuses the question', async () => {
    server.use(
      http.get('*/api/matches', () =>
        HttpResponse.json(
          { error: { code: 'internal', message: 'Internal server error', detail: null } },
          { status: 500 },
        ),
      ),
    )
    renderApp('/')

    expect(await screen.findByRole('link', { name: 'New match' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('link', { name: /still playing/i })).not.toBeInTheDocument()
    })
  })
})

describe('the way on to everything else', () => {
  it('offers a new match, the player list and the statistics', async () => {
    servesMatches([])
    renderApp('/')

    for (const [name, href] of [
      ['New match', '/setup'],
      ['Players', '/players'],
      ['Stats', '/stats'],
    ]) {
      expect(await screen.findByRole('link', { name })).toHaveAttribute('href', href)
    }
  })

  it('meets #4’s 56px floor on every target, resume card included', async () => {
    servesMatches([inProgress()])
    renderApp('/')
    await screen.findByRole('link', { name: /still playing/i })

    // jsdom does no layout, so a bounding box is uniformly zero; it does apply
    // the cascade, so the declared minimum is real. It will not resolve
    // `var()`, hence the one level of indirection followed by hand -- the same
    // approach components.test.tsx takes, for the same reason.
    const token = getComputedStyle(document.documentElement).getPropertyValue('--touch-min').trim()
    expect(token).toBe('56px')

    const links = screen.getAllByRole('link')
    expect(links).toHaveLength(4)
    for (const link of links) {
      expect(getComputedStyle(link).getPropertyValue('min-height').trim()).toBe('var(--touch-min)')
    }
  })
})
