import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import '../styles/global.css'
import { Button } from './Button'
import { Chip } from './Chip'
import { Key } from './Key'
import { ScoreCard } from './ScoreCard'
import { SegmentedControl } from './SegmentedControl'
import { Stepper } from './Stepper'

/**
 * The 56px floor, read off computed styles rather than eyeballed.
 *
 * jsdom does no layout, so getBoundingClientRect is uniformly zero and useless
 * here. It does apply the cascade, so the declared min-height/min-width are
 * real. What it will not do is resolve var(), so a declaration of
 * `min-height: var(--touch-min)` comes back as the literal string — hence
 * resolveLength, which follows exactly that one level of indirection against
 * :root. The alternative was hard-coding 56px in every component and letting
 * the token drift out of sync with the thing it is supposed to govern.
 */
function resolveLength(element: Element, property: 'min-height' | 'min-width'): number {
  const declared = getComputedStyle(element).getPropertyValue(property).trim()
  const varMatch = /^var\((--[\w-]+)\)$/.exec(declared)
  const value = varMatch
    ? getComputedStyle(document.documentElement).getPropertyValue(varMatch[1]!).trim()
    : declared

  const px = /^(-?[\d.]+)px$/.exec(value)
  if (px === null) {
    throw new Error(`${property} resolved to ${JSON.stringify(value)}, which is not a px length`)
  }
  return Number(px[1])
}

const MIN_TOUCH = 56

function expectTouchTarget(element: Element) {
  expect(resolveLength(element, 'min-height')).toBeGreaterThanOrEqual(MIN_TOUCH)
  expect(resolveLength(element, 'min-width')).toBeGreaterThanOrEqual(MIN_TOUCH)
}

describe('touch targets', () => {
  it('the token itself is 56px', () => {
    expect(getComputedStyle(document.documentElement).getPropertyValue('--touch-min').trim()).toBe(
      '56px',
    )
  })

  it.each(['primary', 'secondary', 'danger', 'ghost'] as const)(
    'Button/%s meets the 56px floor',
    (variant) => {
      render(
        <Button variant={variant} data-testid="btn">
          Tap
        </Button>,
      )
      expectTouchTarget(screen.getByTestId('btn'))
    },
  )

  it('a disabled Button still meets the floor', () => {
    render(
      <Button disabled data-testid="btn">
        Tap
      </Button>,
    )
    expectTouchTarget(screen.getByTestId('btn'))
  })

  it.each(['default', 'accent', 'danger'] as const)('Key/%s meets the 56px floor', (variant) => {
    render(<Key label="20" variant={variant} data-testid="key" />)
    expectTouchTarget(screen.getByTestId('key'))
  })

  it('every Key in a rendered keypad row meets the floor', () => {
    render(
      <div>
        {[20, 19, 18, 17, 16].map((n) => (
          <Key key={n} label={String(n)} />
        ))}
      </div>,
    )
    const keys = screen.getAllByRole('button')
    expect(keys).toHaveLength(5)
    for (const key of keys) expectTouchTarget(key)
  })

  it('Chip meets the floor', () => {
    render(<Chip label="501" data-testid="chip" />)
    expect(resolveLength(screen.getByTestId('chip'), 'min-height')).toBeGreaterThanOrEqual(
      MIN_TOUCH,
    )
  })

  it('every SegmentedControl option meets the floor', () => {
    render(
      <SegmentedControl
        label="Dart multiplier"
        value="single"
        options={[
          { value: 'single', label: 'Single' },
          { value: 'double', label: 'Double' },
          { value: 'triple', label: 'Triple' },
        ]}
      />,
    )
    for (const option of screen.getAllByRole('radio')) expectTouchTarget(option)
  })

  it('both Stepper buttons meet the floor', () => {
    render(<Stepper label="Legs to win" value={3} min={1} max={9} />)
    expectTouchTarget(screen.getByLabelText('Decrease Legs to win'))
    expectTouchTarget(screen.getByLabelText('Increase Legs to win'))
  })
})

/**
 * `ScoreCard` in the two shapes #24 does not use but #25 and the mockups do.
 *
 * The play screen always supplies a label and an x01 score, so the unlabelled
 * and scoreless paths would otherwise go unexercised -- and they are exactly the
 * paths #25 needs, since a cricket team has marks where a score would be.
 */
describe('the score card', () => {
  it('renders an em dash for a team with no score, as cricket has', () => {
    render(<ScoreCard name="Jack" score={null} />)

    expect(screen.getByText('—')).toBeInTheDocument()
    // No label supplied, so it is not a group and announces as plain content.
    expect(screen.queryByRole('group')).not.toBeInTheDocument()
  })

  it('shows an average only once there is one', () => {
    const { rerender } = render(<ScoreCard name="Jack" score={501} average={null} legs={0} />)
    // Null is "has not thrown", and 0.0 would read as a bad average instead.
    expect(screen.queryByText(/^Avg/)).not.toBeInTheDocument()
    expect(screen.getByText('Legs 0')).toBeInTheDocument()

    rerender(<ScoreCard name="Jack" score={441} average={58.35} legs={1} />)
    // Rounded for display by the card, not by the caller sending fewer digits.
    expect(screen.getByText('Avg 58.4')).toBeInTheDocument()
  })
})
