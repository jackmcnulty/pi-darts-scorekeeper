/// <reference types="vitest/config" />
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const swEntry = fileURLToPath(new URL('./src/sw/sw.ts', import.meta.url))

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // scripts/dev.sh runs Uvicorn on :8000; everything under /api goes there.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      // Two entries: the app, and the service worker.
      input: { index: 'index.html', sw: swEntry },
      output: {
        // `sw.js` must keep that exact name at the root of dist. A hashed
        // service worker could never be registered by a fixed URL, and one
        // under /assets/ would be scoped to /assets/ and see none of the
        // navigations it exists to handle.
        //
        // It is also the one file that must not be cached forever:
        // darts.api.static sends `no-cache` for anything unhashed, which is
        // exactly right here -- it is how an updated worker is ever noticed.
        entryFileNames: (chunk) => (chunk.name === 'sw' ? 'sw.js' : 'assets/[name]-[hash].js'),
        // Rollup would otherwise be free to lift handler.ts into a shared
        // chunk that sw.js imports by a hashed name. Registration would then
        // depend on a second file whose name the worker cannot predict.
        manualChunks: undefined,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    // Components declare their touch targets in CSS, so the cascade has to be
    // present for the computed-style assertions in components.test.tsx.
    css: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/test-setup.ts',
        'src/vite-env.d.ts',
        // The two files that exist only to be wired up, and cannot run under
        // jsdom: main.tsx needs a real document to mount into, and sw.ts
        // needs a ServiceWorkerGlobalScope. Every decision either of them
        // would make lives in a module that is covered -- sw.ts's in
        // src/sw/handler.ts especially, which #21 requires be tested.
        'src/main.tsx',
        'src/sw/sw.ts',
      ],
      thresholds: {
        // Set at 85 to match the backend's --cov-fail-under, and set just
        // under where the suite actually sits so it is a ratchet against
        // regression rather than a number to aim at.
        lines: 85,
        statements: 85,
        functions: 85,
        branches: 85,
      },
    },
  },
})
