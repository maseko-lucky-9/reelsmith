import { describe, it, expect } from 'vitest'
import { createMemoryHistory, createRouter } from '@tanstack/react-router'
import { routeTree } from './routeTree'

describe('routeTree', () => {
  const router = createRouter({ routeTree, history: createMemoryHistory() })
  const paths = Object.keys(router.routesByPath)

  it('has no /calendar page: scheduled publishing was dropped (FR-032, T045)', () => {
    // Reads the real tree, not an empty map.
    expect(paths).toEqual(expect.arrayContaining(['/', '/jobs/$jobId', '/settings/social']))
    expect(paths).not.toContain('/calendar')
  })
})
