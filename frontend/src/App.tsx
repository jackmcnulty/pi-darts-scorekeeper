/**
 * Every address this app answers to.
 *
 * The router lives outside this component -- `main.tsx` supplies a
 * `BrowserRouter`, tests supply a `MemoryRouter` -- so that a test can open a
 * deep link without a real history stack, and so that the route table is one
 * thing rather than a thing wrapped in a thing.
 *
 * #21 made every path real and every screen a `Placeholder`; #22 replaced the
 * first two with `Home` and `Players`, and #23 replaced `/setup`. The
 * remaining placeholders each name the ticket that replaces them. The paths themselves were the part worth getting
 * right early, because #16's SPA fallback means the server will hand
 * `index.html` to any of them on a cold reload and the client alone has to
 * agree about what they mean.
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
import { History } from './routes/History'
import { Home } from './routes/Home'
import { MatchDetail } from './routes/MatchDetail'
import { NotFound } from './routes/NotFound'
import { Placeholder } from './routes/Placeholder'
import { Play } from './routes/Play'
import { Players } from './routes/Players'
import { RootLayout } from './routes/RootLayout'
import { Setup } from './routes/Setup'

export default function App() {
  return (
    <Routes>
      <Route element={<RootLayout />}>
        <Route index element={<Home />} />
        <Route path="players" element={<Players />} />
        <Route path="setup" element={<Setup />} />
        {/* One path for both game types: #24 is x01 and #25 is cricket, but a
            match knows which it is, and the player only ever taps "play". #24
            reads `config.game_type` and gives a cricket match a notice naming
            #25, rather than drawing an x01 board for a game that has none. */}
        <Route path="play/:matchId" element={<Play />} />
        {/* #21 mounted both of these as placeholders at exactly these paths, so
            that "reloading /history/42 loads that screen" was testable before
            the screen existed. #26 replaces them in place. */}
        <Route path="history" element={<History />} />
        <Route path="history/:matchId" element={<MatchDetail />} />
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
