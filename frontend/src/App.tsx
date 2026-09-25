/**
 * Every address this app answers to.
 *
 * The router lives outside this component -- `main.tsx` supplies a
 * `BrowserRouter`, tests supply a `MemoryRouter` -- so that a test can open a
 * deep link without a real history stack, and so that the route table is one
 * thing rather than a thing wrapped in a thing.
 *
 * #21 made every path real and every screen a `Placeholder`; #22 replaced the
 * first two with `Home` and `Players`, #23 replaced `/setup`, #24 and #25
 * `/play/:matchId`, #26 the two history screens and #27 the last one. There are
 * no placeholders left, so the component is gone with them -- `NotFound` keeps
 * its stylesheet, being the one screen that is still a sentence and a link. The
 * paths themselves were the part worth getting right early, because #16's SPA
 * fallback means the server will hand `index.html` to any of them on a cold
 * reload and the client alone has to agree about what they mean.
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
import { Play } from './routes/Play'
import { PlayerStats } from './routes/PlayerStats'
import { Players } from './routes/Players'
import { RootLayout } from './routes/RootLayout'
import { Setup } from './routes/Setup'
import { Stats } from './routes/Stats'

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
        {/* #21 mounted `stats` as a placeholder too; #27 replaces it and adds the
            per-player card beside it. The pair mirrors `history` +
            `history/:matchId` deliberately: a leaderboard row opens a card, and a
            card is an address somebody can send. Both keep their filters in the
            query string, so `/stats?game_type=cricket` survives a reload -- which
            `history` does not do and was not asked to. */}
        <Route path="stats" element={<Stats />} />
        <Route path="stats/:playerId" element={<PlayerStats />} />

        <Route path="style" element={<StyleGuide />} />
        <Route path="style/x01" element={<X01Mockup />} />
        <Route path="style/cricket" element={<CricketMockup />} />
        <Route path="style/setup" element={<SetupMockup />} />

        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
