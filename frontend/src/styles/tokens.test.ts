import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import process from 'node:process'
import { describe, expect, it } from 'vitest'

/*
 * Reads tokens.css as text rather than going through jsdom, because jsdom does
 * not resolve var() and we want the ratios checked against the literal values
 * that ship.
 */

// Vitest runs with cwd at the frontend package root. import.meta.url is an http
// URL under the Vite dev transform, so it cannot be used to reach the disk here.
const TOKENS_CSS = readFileSync(resolve(process.cwd(), 'src/styles/tokens.css'), 'utf8')

function parseTokens(css: string): Map<string, string> {
  const tokens = new Map<string, string>()
  const declaration = /(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;/g
  let match: RegExpExecArray | null
  while ((match = declaration.exec(css)) !== null) {
    tokens.set(match[1]!, match[2]!)
  }
  return tokens
}

const TOKENS = parseTokens(TOKENS_CSS)

function rgb(token: string): [number, number, number] {
  const hex = TOKENS.get(token)
  if (hex === undefined) throw new Error(`${token} is not a 6-digit hex token in tokens.css`)
  return [
    parseInt(hex.slice(1, 3), 16) / 255,
    parseInt(hex.slice(3, 5), 16) / 255,
    parseInt(hex.slice(5, 7), 16) / 255,
  ]
}

/** sRGB -> linear-light, the WCAG transfer function. */
function toLinear(channel: number): number {
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
}

function luminance(token: string): number {
  const [r, g, b] = rgb(token).map(toLinear) as [number, number, number]
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(foreground: string, background: string): number {
  const a = luminance(foreground)
  const b = luminance(background)
  const [lighter, darker] = a > b ? [a, b] : [b, a]
  return (lighter + 0.05) / (darker + 0.05)
}

/** Every pair that actually occurs in the UI. Body text and anything load-bearing. */
const PRIMARY_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ['--color-text', '--color-bg'],
  ['--color-text', '--color-surface'],
  ['--color-text', '--color-surface-raised'],
  ['--color-text', '--color-surface-pressed'],
  ['--color-text-on-fill', '--color-primary'],
  ['--color-text-on-fill', '--color-danger'],
  ['--color-text-on-fill', '--color-success'],
  ['--color-text-on-fill', '--color-warning'],
  ['--color-text-on-fill', '--color-text-secondary'],
  ...Array.from({ length: 8 }, (_, i) => ['--color-text-on-fill', `--accent-${i + 1}`] as const),
]

/** Supporting text and non-essential labels. */
const SECONDARY_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ['--color-text-secondary', '--color-bg'],
  ['--color-text-secondary', '--color-surface'],
  ['--color-text-secondary', '--color-surface-raised'],
  ['--color-text-secondary', '--color-surface-pressed'],
  ['--color-text-muted', '--color-bg'],
  ['--color-text-muted', '--color-surface'],
  ['--color-text-muted', '--color-surface-raised'],
  ['--color-primary', '--color-bg'],
  ['--color-primary', '--color-surface'],
  ['--color-danger', '--color-surface'],
  ['--color-danger', '--color-surface-raised'],
  ['--color-success', '--color-surface'],
  ['--color-success', '--color-surface-raised'],
  ['--color-warning', '--color-surface'],
]

describe('token contrast', () => {
  it('parses every colour token out of tokens.css', () => {
    expect(TOKENS.size).toBeGreaterThan(15)
    expect(TOKENS.get('--color-bg')).toBe('#0b0e13')
  })

  it.each(PRIMARY_PAIRS)('%s on %s is at least 7:1', (foreground, background) => {
    expect(contrast(foreground, background)).toBeGreaterThanOrEqual(7)
  })

  it.each(SECONDARY_PAIRS)('%s on %s is at least 4.5:1', (foreground, background) => {
    expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5)
  })
})

/*
 * Colour-vision deficiency.
 *
 * Player accents are how you tell whose score is whose, so "8 distinct colours"
 * has to survive protanopia, deuteranopia and tritanopia. Simulate each with the
 * Machado et al. (2009) severity-1.0 matrices in linear-light RGB, convert to
 * CIELAB, and require every pair to stay apart by a CIE76 delta-E.
 */

type Matrix = readonly [
  readonly [number, number, number],
  readonly [number, number, number],
  readonly [number, number, number],
]

const CVD_KINDS = ['protanopia', 'deuteranopia', 'tritanopia'] as const
type CvdKind = (typeof CVD_KINDS)[number]

const CVD_MATRICES: Record<CvdKind, Matrix> = {
  protanopia: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deuteranopia: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
  tritanopia: [
    [1.255528, -0.076749, -0.178779],
    [-0.078411, 0.930809, 0.147602],
    [0.004733, 0.691367, 0.3039],
  ],
}

function applyMatrix(m: Matrix, [r, g, b]: [number, number, number]): [number, number, number] {
  return [
    m[0][0] * r + m[0][1] * g + m[0][2] * b,
    m[1][0] * r + m[1][1] * g + m[1][2] * b,
    m[2][0] * r + m[2][1] * g + m[2][2] * b,
  ]
}

/** Linear-light RGB -> CIEXYZ (sRGB primaries, D65). */
function toXyz([r, g, b]: [number, number, number]): [number, number, number] {
  return [
    0.4124564 * r + 0.3575761 * g + 0.1804375 * b,
    0.2126729 * r + 0.7151522 * g + 0.072175 * b,
    0.0193339 * r + 0.119192 * g + 0.9503041 * b,
  ]
}

const D65: readonly [number, number, number] = [0.95047, 1, 1.08883]

function toLab(xyz: [number, number, number]): [number, number, number] {
  // The /116 belongs to the linear branch only, not to the cube-root branch.
  const f = (t: number) => (t > 216 / 24389 ? Math.cbrt(t) : ((24389 / 27) * t + 16) / 116)
  const [x, y, z] = xyz.map((v, i) => f(v / D65[i]!)) as [number, number, number]
  return [116 * y - 16, 500 * (x - y), 200 * (y - z)]
}

function simulate(token: string, kind: CvdKind): [number, number, number] {
  const linear = rgb(token).map(toLinear) as [number, number, number]
  const clamped = applyMatrix(CVD_MATRICES[kind], linear).map((v) => Math.min(1, Math.max(0, v)))
  return toLab(toXyz(clamped as [number, number, number]))
}

function deltaE([l1, a1, b1]: number[], [l2, a2, b2]: number[]): number {
  return Math.hypot(l1! - l2!, a1! - a2!, b1! - b2!)
}

const ACCENTS = Array.from({ length: 8 }, (_, i) => `--accent-${i + 1}`)

/**
 * CIE76 delta-E. ~2.3 is "just noticeable". The palette was chosen by search to
 * maximise this; it measures 15.97 at worst, so 15 locks that in with a little
 * slack for rounding without letting the palette quietly degrade.
 */
const MIN_DELTA_E = 15

describe('player accents under colour-vision deficiency', () => {
  it('declares eight accents', () => {
    for (const accent of ACCENTS) expect(TOKENS.get(accent)).toMatch(/^#[0-9a-f]{6}$/)
  })

  it.each(CVD_KINDS)('all eight stay distinguishable under %s', (kind) => {
    const failures: string[] = []
    for (let i = 0; i < ACCENTS.length; i++) {
      for (let j = i + 1; j < ACCENTS.length; j++) {
        const a = ACCENTS[i]!
        const b = ACCENTS[j]!
        const distance = deltaE(simulate(a, kind), simulate(b, kind))
        if (distance < MIN_DELTA_E) {
          failures.push(`${a} vs ${b}: deltaE ${distance.toFixed(1)}`)
        }
      }
    }
    expect(failures).toEqual([])
  })
})
