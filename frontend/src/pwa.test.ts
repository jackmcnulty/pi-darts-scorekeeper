// @vitest-environment node
/**
 * Everything the PWA promises is actually in the build.
 *
 * This test exists because the server structurally cannot tell you otherwise.
 * `darts.api.static.SpaStaticFiles` turns any non-`/api` 404 into `index.html`
 * with a 200, which is what makes `/history/42` survive a reload -- and also
 * means a missing `apple-touch-icon.png` comes back as `200 text/html` rather
 * than as a 404. A mistyped icon shows up as a blank home-screen tile weeks
 * later; a missing `sw.js` shows up as a service worker that refuses to
 * register on a MIME-type mismatch, which reads as a script error rather than
 * as "you forgot to build it".
 *
 * So nothing here trusts the file list in `public/`. It runs a real Vite
 * build, reads the emitted `index.html` and manifest, and resolves every
 * asset they reference against what the build actually wrote.
 */
import { mkdtemp, readFile, rm, stat } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { build } from 'vite'
import { HASHED, PRECACHE } from './sw/handler'

const root = fileURLToPath(new URL('..', import.meta.url))

let out: string
let indexHtml: string

beforeAll(async () => {
  out = await mkdtemp(join(tmpdir(), 'darts-dist-'))
  await build({ root, build: { outDir: out, emptyOutDir: true }, logLevel: 'silent' })
  indexHtml = await readFile(join(out, 'index.html'), 'utf8')
  // A generous ceiling, not a performance assertion: the build is ~100ms
  // locally and this only needs to outlast a cold CI runner.
}, 120_000)

afterAll(async () => {
  await rm(out, { recursive: true, force: true })
})

async function emitted(path: string): Promise<boolean> {
  return stat(join(out, path))
    .then((entry) => entry.isFile())
    .catch(() => false)
}

/** Every root-relative URL `index.html` points at. */
function referencedByHtml(): string[] {
  return [...indexHtml.matchAll(/(?:href|src)="(\/[^"]+)"/g)]
    .map((match) => match[1])
    .filter((url): url is string => url !== undefined)
}

describe('the built index.html', () => {
  it('references something, so the assertions below are not vacuous', () => {
    expect(referencedByHtml().length).toBeGreaterThanOrEqual(4)
  })

  it('points only at files the build actually wrote', async () => {
    for (const url of referencedByHtml()) {
      expect(await emitted(url), `${url} is referenced but was not emitted`).toBe(true)
    }
  })

  it('keeps #4’s iOS meta tags', () => {
    // The half of the PWA work that landed in #4. A build that dropped them
    // would install to the home screen and still launch inside Safari chrome.
    expect(indexHtml).toContain('viewport-fit=cover')
    expect(indexHtml).toContain('name="apple-mobile-web-app-capable"')
    expect(indexHtml).toContain('content="black-translucent"')
    expect(indexHtml).toContain('name="apple-mobile-web-app-title"')
  })

  it('names the manifest and the apple-touch-icon', () => {
    expect(indexHtml).toContain('rel="manifest"')
    expect(indexHtml).toContain('rel="apple-touch-icon"')
  })
})

describe('the manifest', () => {
  it('asks for a standalone dark launch', async () => {
    const manifest: unknown = JSON.parse(await readFile(join(out, 'manifest.webmanifest'), 'utf8'))
    expect(manifest).toMatchObject({
      display: 'standalone',
      theme_color: '#0b0e13',
      start_url: '/',
      scope: '/',
    })
  })

  it('declares maskable icons at both sizes, and emits them', async () => {
    const manifest = JSON.parse(await readFile(join(out, 'manifest.webmanifest'), 'utf8')) as {
      icons: { src: string; sizes: string; purpose: string }[]
    }

    expect(manifest.icons.map((icon) => icon.sizes).sort()).toEqual(['192x192', '512x512'])
    for (const icon of manifest.icons) {
      expect(icon.purpose).toContain('maskable')
      expect(await emitted(icon.src), `${icon.src} is in the manifest but was not emitted`).toBe(
        true,
      )
    }
  })
})

describe('the service worker', () => {
  it('is emitted at the root under its own fixed name', async () => {
    // Not hashed: a service worker has to be registered by a URL known ahead
    // of time. Not under /assets/: its scope would then be /assets/ and it
    // would never see a navigation.
    expect(await emitted('sw.js')).toBe(true)
  })

  it('is self-contained, so registration depends on no second file', async () => {
    // Rollup is free to lift shared code into its own hashed chunk. If it
    // ever did that to handler.ts, sw.js would import a name it cannot know.
    const source = await readFile(join(out, 'sw.js'), 'utf8')
    expect(source).not.toMatch(/\bimport\s*[({'"]/)
    expect(source).not.toMatch(/\bfrom\s*['"]/)
  })

  it('precaches only files the build actually wrote', async () => {
    // `cache.addAll` is all-or-nothing: one missing entry rejects the whole
    // install, the worker never activates, and the app silently stays
    // online-only. Nothing would 404 to tell you -- the SPA fallback would
    // hand back index.html with a 200 and the wrong content type.
    for (const entry of PRECACHE) {
      const path = entry === '/' ? 'index.html' : entry
      expect(await emitted(path), `${entry} is precached but was not emitted`).toBe(true)
    }
  })

  it('carries the /api rule into the emitted bundle', async () => {
    // handler.test.ts proves the rule. This proves the rule survived being
    // bundled, rather than being dropped as unreachable.
    const source = await readFile(join(out, 'sw.js'), 'utf8')
    expect(source).toContain('/api/')
  })
})

describe('what the server will send cache headers for', () => {
  it('emits the app’s own code hashed, so it can be cached forever', () => {
    const hashed = referencedByHtml().filter((url) => HASHED.test(url))
    expect(hashed.length).toBeGreaterThan(0)
  })

  it('leaves the files that must be revalidated unhashed', async () => {
    // darts.api.static sends `no-cache` for anything unhashed. These four
    // have to be in that group: a hashed manifest could not be linked, and a
    // forever-cached sw.js could never be updated.
    for (const path of ['index.html', 'sw.js', 'manifest.webmanifest', 'apple-touch-icon.png']) {
      expect(await emitted(path), `${path} was not emitted`).toBe(true)
      expect(HASHED.test(`/${path}`), `${path} must stay unhashed`).toBe(false)
    }
  })
})
