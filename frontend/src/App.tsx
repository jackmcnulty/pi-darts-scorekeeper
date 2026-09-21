import { useEffect, useState } from 'react'
import { CricketMockup } from './style/mockups/CricketMockup'
import { SetupMockup } from './style/mockups/SetupMockup'
import { X01Mockup } from './style/mockups/X01Mockup'
import { StyleGuide } from './style/StyleGuide'

/**
 * Deliberately not a router. Real routing is #21; this ticket only owes a
 * `/style` route, and the mockups hang off it by hash so there is still only
 * one route to serve. Back is Safari's back gesture.
 */
type View = 'components' | 'x01' | 'cricket' | 'setup'

function viewFromHash(hash: string): View {
  switch (hash.replace(/^#/, '')) {
    case 'x01':
      return 'x01'
    case 'cricket':
      return 'cricket'
    case 'setup':
      return 'setup'
    default:
      return 'components'
  }
}

function useHashView(): View {
  const [view, setView] = useState<View>(() => viewFromHash(window.location.hash))

  useEffect(() => {
    const onHashChange = () => {
      setView(viewFromHash(window.location.hash))
    }
    window.addEventListener('hashchange', onHashChange)
    return () => {
      window.removeEventListener('hashchange', onHashChange)
    }
  }, [])

  return view
}

export default function App() {
  const view = useHashView()

  switch (view) {
    case 'x01':
      return <X01Mockup />
    case 'cricket':
      return <CricketMockup />
    case 'setup':
      return <SetupMockup />
    case 'components':
      return <StyleGuide />
  }
}
