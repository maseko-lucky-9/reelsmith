import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ComponentType } from 'react'

vi.mock('@/api/client', () => ({
  api: {
    listBrandTemplates: vi.fn().mockResolvedValue([]),
    createBrandTemplate: vi.fn(),
    updateBrandTemplate: vi.fn(),
    deleteBrandTemplate: vi.fn(),
  },
}))

import { brandTemplateRoute } from './settings.brand'
import { captionsSettingsRoute } from './settings.captions'
import { webhooksSettingsRoute } from './settings.webhooks'

const LABEL = 'Not applied to renders yet'

function renderPage(route: { options: { component?: unknown } }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Page = route.options.component as ComponentType
  return render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
}

describe('settings that do not affect renders are labelled', () => {
  it('brand template page shows the label in the header', () => {
    renderPage(brandTemplateRoute)
    const heading = screen.getByRole('heading', { name: 'Brand template' })
    const header = heading.parentElement?.parentElement as HTMLElement
    expect(within(header).getByText(LABEL)).toBeTruthy()
  })

  it('the Auto transitions toggle carries its own label', () => {
    renderPage(brandTemplateRoute)
    const toggle = screen.getByRole('switch', { name: 'Auto transitions' })
    const row = toggle.parentElement as HTMLElement
    expect(within(row).getByText(LABEL)).toBeTruthy()
  })

  it('caption templates page shows the label', () => {
    renderPage(captionsSettingsRoute)
    expect(screen.getByText(LABEL)).toBeTruthy()
  })
})

describe('webhooks page', () => {
  it('says webhooks are unavailable and does not cite a nonexistent endpoint', () => {
    const { container } = renderPage(webhooksSettingsRoute)
    expect(screen.getByText(/Webhooks are not available yet/)).toBeTruthy()
    expect(container.textContent).not.toContain('POST /api/webhooks')
  })
})
