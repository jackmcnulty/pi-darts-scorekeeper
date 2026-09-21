import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('App', () => {
  it('renders the root component', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: /get started/i })).toBeInTheDocument()
  })

  it('renders the counter button', () => {
    render(<App />)
    expect(screen.getByRole('button', { name: /count is 0/i })).toBeInTheDocument()
  })
})
