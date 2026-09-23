/**
 * The mount point, and the order the shell is assembled in.
 *
 * Outermost first, because each layer only protects what is inside it:
 *
 * 1. `ErrorBoundary` -- outside the router and the query provider, so a bug
 *    in either is caught rather than being the thing that white-screens.
 * 2. `QueryClientProvider` -- one client for the process. Built here rather
 *    than at module scope so the app owns exactly one and tests can build
 *    their own without sharing a cache.
 * 3. `BrowserRouter` -- real URLs, which is what #16's SPA fallback exists to
 *    make survivable across a reload.
 * 4. `App` -- the route table.
 *
 * `StrictMode` stays on. It double-invokes renders in development, which is
 * how an effect that fires twice gets found on a laptop rather than on a
 * phone halfway through a leg.
 */
import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { createQueryClient } from './api/queryClient'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary'
import { registerServiceWorker } from './sw/register'
import './styles/global.css'

const rootElement = document.getElementById('root')
if (rootElement === null) throw new Error('#root is missing from index.html')

const queryClient = createQueryClient()

createRoot(rootElement).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)

registerServiceWorker()
