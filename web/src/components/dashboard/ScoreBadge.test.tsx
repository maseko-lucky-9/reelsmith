import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { ScoreBadge } from './ScoreBadge'

describe('ScoreBadge', () => {
  it('renders — for null score', () => {
    render(<ScoreBadge score={null} />)
    expect(screen.getByText('—')).toBeDefined()
  })

  it('renders — for undefined score', () => {
    render(<ScoreBadge score={undefined} />)
    expect(screen.getByText('—')).toBeDefined()
  })

  it('shows the score value as a plain number', () => {
    render(<ScoreBadge score={75} />)
    expect(screen.getByText('75')).toBeDefined()
  })

  it('applies score-green color via inline style', () => {
    const { container } = render(<ScoreBadge score={80} />)
    const span = container.firstChild as HTMLElement
    expect(span.style.color).toBe('var(--score-green)')
  })

  it('applies bold font weight', () => {
    const { container } = render(<ScoreBadge score={50} />)
    const span = container.firstChild as HTMLElement
    expect(span.style.fontWeight).toBe('700')
  })

  it('does NOT render a pill badge (no background color class)', () => {
    const { container } = render(<ScoreBadge score={90} />)
    const span = container.firstChild as HTMLElement
    expect(span.className).not.toContain('bg-emerald')
    expect(span.className).not.toContain('bg-amber')
    expect(span.className).not.toContain('bg-red')
  })

  it('accepts a custom className', () => {
    const { container } = render(<ScoreBadge score={42} className="text-7xl font-bold" />)
    const span = container.firstChild as HTMLElement
    expect(span.className).toContain('text-7xl')
  })
})
