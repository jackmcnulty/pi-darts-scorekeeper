/**
 * Every address this app answers to.
 *
 * The router lives outside this component -- `main.tsx` supplies a
 * `BrowserRouter`, tests supply a `MemoryRouter` -- so that a test can open a
 * deep link without a real history stack, and so that the route table is one
 * thing rather than a thing wrapped in a thing.
 *
 * The paths are real and the screens are not: #21 is the shell, and every
 * `Placeholder` below names the ticket that replaces it. The paths themselves
 * are the part worth getting right now, because #16's SPA fallback means the
 * server will hand `index.html` to any of them on a cold reload and the
 * client has to agree about what they mean.
 *
 * `/style` survives from #4. The style guide and the three mockups are the
 * artefact that was approved on a real iPhone, and they stay reachable on the
 * Pi while #22 onwards are built against them.
 */
import { Route, Routes } from 'react-router'
import { CricketMockup } from './style/mockups/CricketMockup'
import { SetupMockup } from './style/mockups/SetupMockup'
import { X01Mockup } from './style/mockups/X01Mockup'
import { StyleGuide } from './style/StyleGuide'
import { NotFound } from './routes/NotFound'
import { Placeholder } from './routes/Placeholder'
import { RootLayout } from './routes/RootLayout'

export default function App() {
  return (
    <Routes>
      <Route element={<RootLayout />}>
        <Route index element={<Placeholder title="Darts" ticket="#22" />} />
        <Route path="players" element={<Placeholder title="Players" ticket="#22" />} />
        <Route path="setup" element={<Placeholder title="New match" ticket="#23" />} />
        {/* One path for both game types: #24 is x01 and #25 is cricket, but a
            match knows which it is, and the player only ever taps "play". */}
        <Route path="play/:matchId" element={<Placeholder title="Play" ticket="#24 / #25" />} />
        <Route path="history" element={<Placeholder title="History" ticket="#26" />} />
        <Route path="history/:matchId" element={<Placeholder title="Match" ticket="#26" />} />
        <Route path="stats" element={<Placeholder title="Stats" ticket="#27" />} />

        <Route path="style" element={<StyleGuide />} />
        <Route path="style/x01" element={<X01Mockup />} />
        <Route path="style/cricket" element={<CricketMockup />} />
        <Route path="style/setup" element={<SetupMockup />} />

        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
