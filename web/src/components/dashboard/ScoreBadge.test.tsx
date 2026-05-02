import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { ScoreBadge } from './ScoreBadge'

describe('ScoreBadge', () => {
  it('renders — for null score', () => {
    render(<ScoreBadge score={null} />)
    expect(screen.getByText('—')).toBeDefined()
  })

  it('applies green class for high score', () => {
    const { container } = render(<ScoreBadge score={80} />)
    const badge = container.firstChild as HTMLElement
    expect(badge.className).toContain('bg-emerald')
  })

  it('applies amber class for medium score', () => {
    const { container } = render(<ScoreBadge score={50} />)
    const badge = container.firstChild as HTMLElement
    expect(badge.className).toContain('bg-amber')
  })

  it('applies red class for low score', () => {
    const { container } = render(<ScoreBadge score={10} />)
    const badge = container.firstChild as HTMLElement
    expect(badge.className).toContain('bg-red')
  })

  it('shows the score value', () => {
    render(<ScoreBadge score={75} />)
    expect(screen.getByText('75')).toBeDefined()
  })
})
