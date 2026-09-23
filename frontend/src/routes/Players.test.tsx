/**
 * Player management, against a Pi that behaves like the real one.
 *
 * The handlers here keep a list in memory and enforce the two rules the screen
 * has to cope with -- a repeated name is a 409 carrying `duplicate_name`, and
 * `include_archived` decides who is listed -- because a handler that always
 * said yes would let a screen pass while mishandling every refusal.
 */
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'
import type { components } from '../api/schema'
import '../styles/global.css'
import { installServer, renderApp, server } from '../test-harness'

installServer()

type Player = components['schemas']['PlayerResponse']

function player(id: number, display_name: string, over: Partial<Player> = {}): Player {
  return {
    id,
    display_name,
    short_name: null,
    accent_index: id,
    is_archived: false,
    created_at: '2026-09-23T19:00:00.000Z',
    ...over,
  }
}

/** The 409 the real server sends, built fresh each time: a Response body can
 *  only be read once, and a shared instance is consumed by the first request. */
function conflict() {
  return HttpResponse.json(
    {
      error: {
        code: 'conflict',
        message: "'Dad' is already taken by player 2",
        detail: { reason: 'duplicate_name' },
      },
    },
    { status: 409 },
  )
}

let roster: Player[]
/** Every request body the screen sent, in order, for asserting on the wire. */
let sent: unknown[]

beforeEach(() => {
  roster = [player(1, 'Jack'), player(2, 'Dad')]
  sent = []
  server.use(
    http.get('*/api/players', ({ request }) => {
      const all = new URL(request.url).searchParams.get('include_archived') === 'true'
      return HttpResponse.json(all ? roster : roster.filter((p) => !p.is_archived))
    }),
    http.post('*/api/players', async ({ request }) => {
      const body = (await request.json()) as Player
      sent.push(body)
      const taken = roster.some(
        (p) => !p.is_archived && p.display_name.toLowerCase() === body.display_name.toLowerCase(),
      )
      if (taken) return conflict()
      const created = player(roster.length + 1, body.display_name, {
        short_name: body.short_name,
        accent_index: body.accent_index,
      })
      roster = [...roster, created]
      return HttpResponse.json(created, { status: 201 })
    }),
    http.patch('*/api/players/:id', async ({ request, params }) => {
      const body = (await request.json()) as Player
      sent.push(body)
      const id = Number(params.id)
      const taken = roster.some(
        (p) =>
          p.id !== id &&
          !p.is_archived &&
          p.display_name.toLowerCase() === body.display_name.toLowerCase(),
      )
      if (taken) return conflict()
      const edited = { ...roster.find((p) => p.id === id)!, ...body }
      roster = roster.map((p) => (p.id === id ? edited : p))
      return HttpResponse.json(edited)
    }),
    http.post('*/api/players/:id/archive', ({ params }) => {
      const id = Number(params.id)
      const archived = { ...roster.find((p) => p.id === id)!, is_archived: true }
      roster = roster.map((p) => (p.id === id ? archived : p))
      return HttpResponse.json(archived)
    }),
  )
})

/** Open the screen and wait for the list itself, whoever happens to be on it. */
async function openPlayers() {
  const user = userEvent.setup()
  renderApp('/players')
  await screen.findByRole('list')
  return user
}

describe('the list', () => {
  it('shows every active player with their colour', async () => {
    await openPlayers()

    expect(screen.getByRole('heading', { name: 'Players' })).toBeInTheDocument()
    const jack = screen.getByRole('button', { name: 'Jack' })
    // The dot carries the palette index as a custom property, so a player with
    // no colour falls back in CSS rather than borrowing somebody else's.
    expect(jack.querySelector('.players__dot')?.getAttribute('style')).toContain(
      '--player-accent: var(--accent-1)',
    )
    expect(screen.getByRole('button', { name: 'Dad' })).toBeInTheDocument()
  })

  it('shows a short name beside the full one', async () => {
    roster = [player(1, 'Jack'), player(2, 'Bartholomew', { short_name: 'Bart' })]
    await openPlayers()

    expect(screen.getByRole('button', { name: 'Bartholomew, Bart' })).toBeInTheDocument()
  })

  it('says so, rather than showing an empty list, when nobody has been added', async () => {
    roster = []
    renderApp('/players')

    expect(await screen.findByText(/nobody yet/i)).toBeInTheDocument()
  })

  it('leaves a player from before #22 without a colour rather than inventing one', async () => {
    roster = [player(1, 'Jack', { accent_index: null })]
    await openPlayers()

    const dot = screen.getByRole('button', { name: 'Jack' }).querySelector('.players__dot')
    expect(dot?.getAttribute('style') ?? '').not.toContain('--player-accent')
  })

  it('offers a way back when the Pi will not answer', async () => {
    server.use(
      http.get('*/api/players', () =>
        HttpResponse.json(
          { error: { code: 'service_unavailable', message: 'The database is unavailable' } },
          { status: 503 },
        ),
      ),
    )
    renderApp('/players')

    // Generous because the client retries a 503 three times before giving up,
    // which is #21's policy and not something this screen decides.
    expect(await screen.findByRole('alert', {}, { timeout: 5000 })).toHaveTextContent(
      /database is unavailable/i,
    )
    // And the way back actually asks again, rather than being decoration.
    const user = userEvent.setup()
    server.use(http.get('*/api/players', () => HttpResponse.json(roster)))
    await user.click(screen.getByRole('button', { name: /try again/i }))
    expect(await screen.findByRole('button', { name: 'Jack' })).toBeInTheDocument()
  })
})

describe('archiving', () => {
  it('hides an archived player from the default list, which is the picker’s list', async () => {
    roster = [player(1, 'Jack'), player(2, 'Dad', { is_archived: true })]
    await openPlayers()

    expect(screen.queryByRole('button', { name: /Dad/ })).not.toBeInTheDocument()
  })

  it('shows them, marked, when asked for', async () => {
    roster = [player(1, 'Jack'), player(2, 'Dad', { is_archived: true })]
    const user = await openPlayers()

    await user.click(screen.getByRole('radio', { name: 'With archived' }))

    const dad = await screen.findByRole('button', { name: 'Dad, archived' })
    expect(dad).toHaveAttribute('data-archived', 'true')
  })

  it('archives only after the choice is confirmed', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Dad' }))
    await user.click(screen.getByRole('button', { name: 'Archive' }))
    // The first tap explains what archiving is and asks again.
    expect(screen.getByText(/keep every match, dart and statistic/i)).toBeInTheDocument()
    expect(roster[1]?.is_archived).toBe(false)

    await user.click(screen.getByRole('button', { name: /archive dad for good/i }))

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Dad' })).not.toBeInTheDocument()
    })
    expect(roster[1]?.is_archived).toBe(true)
  })
})

describe('adding somebody', () => {
  it('sends the name, the short name and the chosen colour', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Add' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'Ellie')
    await user.type(screen.getByRole('textbox', { name: 'Short name' }), 'Els')
    await user.click(screen.getByRole('radio', { name: 'Colour 5' }))
    await user.click(screen.getByRole('button', { name: 'Add player' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Ellie, Els' })).toBeInTheDocument()
    })
    expect(sent).toEqual([{ display_name: 'Ellie', short_name: 'Els', accent_index: 5 }])
  })

  it('opens on a colour nobody is using', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Add' }))

    // Jack and Dad hold 1 and 2, so 3 is the lowest one free.
    expect(screen.getByRole('radio', { name: 'Colour 3' })).toBeChecked()
  })

  it('sends no short name when the field is left empty', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Add' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), '  Ellie  ')
    await user.click(screen.getByRole('button', { name: 'Add player' }))

    await waitFor(() => {
      expect(sent).toEqual([{ display_name: 'Ellie', short_name: null, accent_index: 3 }])
    })
  })

  it('cannot be submitted with a blank name', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(screen.getByRole('button', { name: 'Add player' })).toBeDisabled()

    await user.type(screen.getByRole('textbox', { name: 'Name' }), '   ')
    expect(screen.getByRole('button', { name: 'Add player' })).toBeDisabled()
  })
})

describe('a name somebody already has', () => {
  it('lands under the field as an error, not as a crash or a toast', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Add' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'dad')
    await user.click(screen.getByRole('button', { name: 'Add player' }))

    const error = await screen.findByRole('alert')
    expect(error).toHaveTextContent(/already called that/i)
    // Attached to the field, so a screen reader reaches it from the input.
    const name = screen.getByRole('textbox', { name: 'Name' })
    expect(name).toHaveAttribute('aria-invalid', 'true')
    expect(name).toHaveAttribute('aria-describedby', error.id)
    // The sheet stayed open with the typing intact, so the name can be fixed.
    expect(name).toHaveValue('dad')
    // Nothing leaked out as the server's own prose.
    expect(screen.queryByText(/is already taken by player/)).not.toBeInTheDocument()
  })

  it('clears the moment the name is changed', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Add' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'dad')
    await user.click(screen.getByRole('button', { name: 'Add player' }))
    await screen.findByRole('alert')

    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'dy')

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('applies to a rename too', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Jack' }))
    await user.clear(screen.getByRole('textbox', { name: 'Name' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'Dad')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/already called that/i)
  })
})

describe('editing somebody', () => {
  it('opens on what they already are, and saves what changed', async () => {
    roster = [player(1, 'Jack', { short_name: 'JM', accent_index: 4 }), player(2, 'Dad')]
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Jack, JM' }))
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('Jack')
    expect(screen.getByRole('textbox', { name: 'Short name' })).toHaveValue('JM')
    expect(screen.getByRole('radio', { name: 'Colour 4' })).toBeChecked()

    await user.clear(screen.getByRole('textbox', { name: 'Short name' }))
    await user.type(screen.getByRole('textbox', { name: 'Short name' }), 'Jacko')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(sent).toEqual([{ display_name: 'Jack', short_name: 'Jacko', accent_index: 4 }])
    })
  })

  it('does not treat their own colour as a clash', async () => {
    roster = [player(1, 'Jack', { accent_index: 4 }), player(2, 'Dad', { accent_index: 5 })]
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Jack' }))

    expect(screen.getByRole('radio', { name: 'Colour 4' })).toBeChecked()
    expect(screen.queryByText(/already .*colour/i)).not.toBeInTheDocument()
  })

  it('names every holder when more than one shares a colour', async () => {
    roster = [
      player(1, 'Jack', { accent_index: 1 }),
      player(2, 'Dad', { accent_index: 3 }),
      player(3, 'Ellie', { accent_index: 3 }),
    ]
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Jack' }))
    await user.click(screen.getByRole('radio', { name: 'Colour 3, used by Dad and Ellie' }))

    expect(screen.getByText(/already dad and ellie’s colour/i)).toBeInTheDocument()
  })

  it('refuses nothing, but says whose colour it is', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Jack' }))
    await user.click(screen.getByRole('radio', { name: /Colour 2, used by Dad/ }))

    expect(screen.getByText(/already dad’s colour/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      expect(sent).toEqual([{ display_name: 'Jack', short_name: null, accent_index: 2 }])
    })
  })

  it('caps the short name at what the server will accept', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Jack' }))
    await user.type(screen.getByRole('textbox', { name: 'Short name' }), 'Bartholomew')

    expect(screen.getByRole('textbox', { name: 'Short name' })).toHaveValue('Bartholo')
  })

  it('shuts without saving when the sheet is dismissed', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Jack' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'son')
    await user.click(document.querySelector('.sheet__scrim')!)

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(sent).toEqual([])
    expect(screen.getByRole('button', { name: 'Jack' })).toBeInTheDocument()
  })
})

describe('nine players, and a palette eight wide', () => {
  beforeEach(() => {
    roster = Array.from({ length: 8 }, (_, i) => player(i + 1, `P${i + 1}`))
  })

  it('marks every colour as taken and still lets one be chosen', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Add' }))

    // Eight players hold all eight colours, so the suggestion is the clash that
    // hurts least rather than a refusal to add a ninth person.
    const suggested = screen.getByRole('radio', { name: 'Colour 1, used by P1' })
    expect(suggested).toBeChecked()
    expect(screen.getByText(/already p1’s colour/i)).toBeInTheDocument()

    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'Ninth')
    await user.click(screen.getByRole('button', { name: 'Add player' }))

    await waitFor(() => {
      expect(sent).toEqual([{ display_name: 'Ninth', short_name: null, accent_index: 1 }])
    })
  })
})

describe('the touch targets', () => {
  it('meet #4’s 56px floor', async () => {
    const user = await openPlayers()
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(getComputedStyle(document.documentElement).getPropertyValue('--touch-min').trim()).toBe(
      '56px',
    )

    const swatch = screen.getByRole('radio', { name: 'Colour 3' })
    const row = screen.getByRole('button', { name: 'Jack' })
    const input = screen.getByRole('textbox', { name: 'Name' })
    for (const element of [swatch, row, input]) {
      expect(getComputedStyle(element).getPropertyValue('min-height').trim()).toBe(
        'var(--touch-min)',
      )
    }
    expect(getComputedStyle(swatch).getPropertyValue('min-width').trim()).toBe('var(--touch-min)')
  })
})

describe('mounting the sheet', () => {
  it('starts from the player it was opened on, every time', async () => {
    const user = await openPlayers()

    await user.click(screen.getByRole('button', { name: 'Jack' }))
    await user.clear(screen.getByRole('textbox', { name: 'Name' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'Nonsense')
    await user.click(document.querySelector('.sheet__scrim')!)

    await user.click(screen.getByRole('button', { name: 'Dad' }))
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('Dad')
  })
})
