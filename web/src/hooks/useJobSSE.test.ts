import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement } from 'react'
import { useJobSSE } from './useJobSSE'

// jsdom doesn't have EventSource — stub it
class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  private listeners: Record<string, (() => void)[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, fn: () => void) {
    this.listeners[type] = [...(this.listeners[type] ?? []), fn]
  }
  dispatchCustom(type: string) {
    this.listeners[type]?.forEach((fn) => fn())
  }
  close() {}
}

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})

describe('useJobSSE', () => {
  it('opens an EventSource for the given jobId', () => {
    const qc = new QueryClient()
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children)

    renderHook(() => useJobSSE('job-test'), { wrapper })
    expect(FakeEventSource.instances.length).toBeGreaterThan(0)
    expect(FakeEventSource.instances[0].url).toContain('job-test')
  })

  it('does not open EventSource when jobId is undefined', () => {
    const qc = new QueryClient()
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children)

    renderHook(() => useJobSSE(undefined), { wrapper })
    expect(FakeEventSource.instances.length).toBe(0)
  })
})
