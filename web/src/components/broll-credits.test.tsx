import { render, screen, within } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import type { BrollAsset } from '@/api/client'
import { BrollCredits } from './broll-credits'

const SERVER_DIR = '/var/reelsmith-test/broll'

function asset(overrides: Partial<BrollAsset> = {}): BrollAsset {
  return {
    query: 'ocean',
    start: 3,
    duration: 3,
    provider: 'pexels',
    asset_id: '1234',
    author: 'Jane Doe',
    source_url: 'https://www.pexels.com/video/ocean-1234/',
    path: `${SERVER_DIR}/cache/videos/1234.mp4`,
    ...overrides,
  }
}

function localAsset(overrides: Partial<BrollAsset> = {}): BrollAsset {
  return asset({
    query: 'forest',
    provider: 'local',
    asset_id: 'forest.mp4',
    author: '',
    source_url: '',
    path: `${SERVER_DIR}/library/forest.mp4`,
    ...overrides,
  })
}

function creditItems() {
  return within(screen.getByRole('list')).getAllByRole('listitem')
}

describe('BrollCredits', () => {
  it('credits the author of each asset, linked to the source page', () => {
    render(<BrollCredits assets={[asset()]} />)

    expect(screen.getByRole('heading', { name: 'B-roll credits' })).toBeInTheDocument()
    const [item] = creditItems()
    expect(item).toHaveTextContent('Video by Jane Doe on Pexels')
    const link = within(item).getByRole('link', { name: /Jane Doe/ })
    expect(link).toHaveAttribute('href', 'https://www.pexels.com/video/ocean-1234/')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('lists an asset inserted twice once', () => {
    render(
      <BrollCredits
        assets={[asset(), asset({ start: 9 }), asset({ asset_id: '99', author: 'Sam Lee' })]}
      />,
    )

    const items = creditItems()
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('Jane Doe')
    expect(items[1]).toHaveTextContent('Sam Lee')
  })

  it.each([
    ['javascript:alert(1)'],
    ['JavaScript:alert(document.cookie)'],
    [' javascript:alert(1)'],
    ['data:text/html,<script>alert(1)</script>'],
    ['vbscript:msgbox(1)'],
    ['ftp://example.com/video'],
    ['/relative/video'],
  ])('shows the author as plain text for the unsafe URL %j', (url) => {
    const { container } = render(<BrollCredits assets={[asset({ source_url: url })]} />)

    const [item] = creditItems()
    expect(item).toHaveTextContent('Video by Jane Doe on Pexels')
    expect(within(item).queryByRole('link')).toBeNull()
    for (const a of container.querySelectorAll('a')) {
      expect(a.getAttribute('href')).toMatch(/^https?:\/\//)
    }
  })

  it('links an http source URL too', () => {
    render(<BrollCredits assets={[asset({ source_url: 'http://example.com/v/1' })]} />)

    expect(screen.getByRole('link', { name: /Jane Doe/ })).toHaveAttribute(
      'href',
      'http://example.com/v/1',
    )
  })

  it('shows a prominent Pexels link when an asset comes from Pexels', () => {
    render(<BrollCredits assets={[localAsset(), asset()]} />)

    const link = screen.getByRole('link', { name: /Videos provided by Pexels/ })
    expect(link).toHaveAttribute('href', 'https://www.pexels.com')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('shows no Pexels link for another provider', () => {
    render(
      <BrollCredits
        assets={[asset({ provider: 'archive', source_url: 'https://archive.example/v/1' })]}
      />,
    )

    expect(creditItems()[0]).toHaveTextContent('Video by Jane Doe on archive')
    expect(screen.queryByRole('link', { name: /Pexels/ })).toBeNull()
  })

  it('labels a local asset without an author and shows no Pexels link', () => {
    render(<BrollCredits assets={[localAsset()]} />)

    const [item] = creditItems()
    expect(item).toHaveTextContent(/^Local library clip$/)
    expect(within(item).queryByRole('link')).toBeNull()
    expect(screen.queryByRole('link', { name: /Pexels/ })).toBeNull()
    expect(screen.queryByText(/Pexels/)).toBeNull()
  })

  it('never shows the server path or the file name of an asset', () => {
    const { container } = render(<BrollCredits assets={[localAsset(), asset()]} />)

    const html = container.innerHTML
    expect(html).not.toContain(SERVER_DIR)
    expect(html).not.toContain('forest.mp4')
    expect(html).not.toContain('1234.mp4')
  })

  it.each([[undefined], [null], [[]]])('renders nothing for assets %j', (assets) => {
    const { container } = render(<BrollCredits assets={assets} />)

    expect(container).toBeEmptyDOMElement()
  })
})
