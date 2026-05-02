import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, beforeEach } from 'vitest'
import { AnnouncementBanner } from './AnnouncementBanner'

const DISMISS_KEY = 'announcement-dismissed'

describe('AnnouncementBanner', () => {
  beforeEach(() => {
    sessionStorage.removeItem(DISMISS_KEY)
  })

  it('renders the banner when not dismissed', () => {
    render(<AnnouncementBanner />)
    expect(screen.getByText(/Star it on/i)).toBeDefined()
  })

  it('hides after clicking dismiss', () => {
    const { container } = render(<AnnouncementBanner />)
    const dismissBtn = container.querySelector('button[aria-label="Dismiss"]')
    expect(dismissBtn).toBeTruthy()
    fireEvent.click(dismissBtn!)
    expect(container.firstChild).toBeNull()
  })

  it('sets sessionStorage key after dismiss', () => {
    const { container } = render(<AnnouncementBanner />)
    const dismissBtn = container.querySelector('button[aria-label="Dismiss"]')
    fireEvent.click(dismissBtn!)
    expect(sessionStorage.getItem(DISMISS_KEY)).toBe('true')
  })

  it('does not render when already dismissed in sessionStorage', () => {
    sessionStorage.setItem(DISMISS_KEY, 'true')
    const { container } = render(<AnnouncementBanner />)
    expect(container.firstChild).toBeNull()
  })
})
