import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'

vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => ({ location: { pathname: '/' } }),
  Link: ({ to, children }: { to: string; children: ReactNode }) =>
    createElement('a', { href: to }, children),
}))

import { Sidebar } from './Sidebar'

describe('Sidebar — only advertises pages that have a backend', () => {
  it('does not link to the Calendar or Analytics pages', () => {
    render(<Sidebar />)
    expect(screen.queryByText('Calendar')).toBeNull()
    expect(screen.queryByText('Analytics')).toBeNull()
    const hrefs = screen.getAllByRole('link').map((a) => a.getAttribute('href'))
    expect(hrefs).not.toContain('/calendar')
    expect(hrefs).not.toContain('/analytics')
  })

  it('still links to the working pages', () => {
    render(<Sidebar />)
    const hrefs = screen.getAllByRole('link').map((a) => a.getAttribute('href'))
    expect(hrefs).toEqual(
      expect.arrayContaining(['/', '/generate/new', '/settings/brand', '/settings/social']),
    )
  })
})
