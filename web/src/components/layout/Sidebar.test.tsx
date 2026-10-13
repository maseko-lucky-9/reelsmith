import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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

function stubViewport(narrow: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn(() => ({
      matches: narrow,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
}

describe('Sidebar — responsive', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    // @ts-expect-error restore jsdom, which has no matchMedia
    delete window.matchMedia
    localStorage.clear()
  })

  const aside = () => screen.getByRole('complementary')
  const toggle = () => screen.getByRole('button', { name: /sidebar/i })

  it('is expanded by default on a wide screen and takes its width from the layout', () => {
    stubViewport(false)
    render(<Sidebar />)
    expect(screen.getByText('Home')).toBeTruthy()
    expect(aside().className).not.toContain('fixed')
    expect(aside().style.width).toBe('220px')
  })

  it('starts collapsed to an icon rail on a narrow screen, even if the saved preference is expanded', () => {
    localStorage.setItem('sidebar-expanded', 'true')
    stubViewport(true)
    render(<Sidebar />)
    expect(screen.queryByText('Home')).toBeNull()
    expect(aside().style.width).toBe('48px')
  })

  it('opens as an overlay on a narrow screen instead of squeezing the page', () => {
    stubViewport(true)
    render(<Sidebar />)
    fireEvent.click(toggle())
    expect(screen.getByText('Home')).toBeTruthy()
    expect(aside().className).toContain('fixed')
    expect(screen.getByTestId('sidebar-backdrop')).toBeTruthy()
  })

  it('closes the overlay from the backdrop', () => {
    stubViewport(true)
    render(<Sidebar />)
    fireEvent.click(toggle())
    fireEvent.click(screen.getByTestId('sidebar-backdrop'))
    expect(screen.queryByText('Home')).toBeNull()
    expect(screen.queryByTestId('sidebar-backdrop')).toBeNull()
  })

  it('does not overwrite the saved desktop preference when used on a narrow screen', () => {
    localStorage.setItem('sidebar-expanded', 'true')
    stubViewport(true)
    render(<Sidebar />)
    fireEvent.click(toggle())
    expect(screen.getByText('Home')).toBeTruthy()
    expect(localStorage.getItem('sidebar-expanded')).toBe('true')
    fireEvent.click(toggle())
    expect(localStorage.getItem('sidebar-expanded')).toBe('true')
  })

  it('still saves the preference when collapsed on a wide screen', () => {
    stubViewport(false)
    render(<Sidebar />)
    fireEvent.click(toggle())
    expect(localStorage.getItem('sidebar-expanded')).toBe('false')
    expect(aside().style.width).toBe('48px')
  })
})

