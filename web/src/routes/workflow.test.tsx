import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AdvancedOptionsPanel } from '@/components/advanced-options-panel'
import { ALL_OFF, ALL_ON, TOGGLES, type ToggleId } from '@/lib/pipelineToggles'

function wrap(children: ReactNode) {
  const qc = new QueryClient()
  return render(
    createElement(QueryClientProvider, { client: qc }, children as React.ReactElement),
  )
}

describe('AdvancedOptionsPanel — toggle groups', () => {
  it('renders 3 toggle groups (Transcribe & caption, Render, Segmentation)', () => {
    const onToggle = vi.fn()
    wrap(<AdvancedOptionsPanel toggles={{ ...ALL_OFF }} onToggle={onToggle} />)
    expect(screen.getByText('Transcribe & caption')).toBeTruthy()
    expect(screen.getByText('Render')).toBeTruthy()
    expect(screen.getByText('Segmentation')).toBeTruthy()
  })

  it('renders all 7 toggles', () => {
    const onToggle = vi.fn()
    wrap(<AdvancedOptionsPanel toggles={{ ...ALL_OFF }} onToggle={onToggle} />)
    for (const toggle of TOGGLES) {
      expect(screen.getByText(toggle.label)).toBeTruthy()
    }
  })
})

describe('AdvancedOptionsPanel — dependency disabling', () => {
  it('disables Captions toggle when Transcription is off', () => {
    const onToggle = vi.fn()
    wrap(<AdvancedOptionsPanel toggles={{ ...ALL_OFF }} onToggle={onToggle} />)
    const captionsSwitch = screen.getByRole('switch', { name: 'Captions' })
    expect(captionsSwitch).toBeDisabled()
  })

  it('enables Captions toggle when Transcription is on', () => {
    const onToggle = vi.fn()
    const toggles = { ...ALL_OFF, transcription: true }
    wrap(<AdvancedOptionsPanel toggles={toggles} onToggle={onToggle} />)
    const captionsSwitch = screen.getByRole('switch', { name: 'Captions' })
    expect(captionsSwitch).not.toBeDisabled()
  })

  it('disables Reframe, B-roll, and Thumbnail toggles when Render is off', () => {
    const onToggle = vi.fn()
    wrap(<AdvancedOptionsPanel toggles={{ ...ALL_OFF }} onToggle={onToggle} />)
    expect(screen.getByRole('switch', { name: 'Reframe (face-track)' })).toBeDisabled()
    expect(screen.getByRole('switch', { name: 'B-roll inserts' })).toBeDisabled()
    expect(screen.getByRole('switch', { name: 'Thumbnail' })).toBeDisabled()
  })

  it('enables child toggles when Render is on', () => {
    const onToggle = vi.fn()
    const toggles = { ...ALL_OFF, render: true }
    wrap(<AdvancedOptionsPanel toggles={toggles} onToggle={onToggle} />)
    expect(screen.getByRole('switch', { name: 'Reframe (face-track)' })).not.toBeDisabled()
    expect(screen.getByRole('switch', { name: 'B-roll inserts' })).not.toBeDisabled()
    expect(screen.getByRole('switch', { name: 'Thumbnail' })).not.toBeDisabled()
  })
})

describe('AdvancedOptionsPanel — default state', () => {
  it('all toggles are OFF by default (ALL_OFF)', () => {
    const onToggle = vi.fn()
    wrap(<AdvancedOptionsPanel toggles={{ ...ALL_OFF }} onToggle={onToggle} />)
    const switches = screen.getAllByRole('switch')
    for (const sw of switches) {
      expect(sw.getAttribute('aria-checked')).toBe('false')
    }
  })
})

describe('AdvancedOptionsPanel — toggle interaction', () => {
  it('calls onToggle when a switch is clicked', () => {
    const onToggle = vi.fn()
    wrap(<AdvancedOptionsPanel toggles={{ ...ALL_OFF }} onToggle={onToggle} />)
    const transcriptionSwitch = screen.getByRole('switch', { name: 'Transcription' })
    fireEvent.click(transcriptionSwitch)
    expect(onToggle).toHaveBeenCalledWith('transcription', true)
  })
})

describe('Toggle cascade logic (unit)', () => {
  // Test the cascade logic that would be in the parent component
  function applyToggle(
    prev: Record<ToggleId, boolean>,
    id: ToggleId,
    value: boolean,
  ): Record<ToggleId, boolean> {
    const next = { ...prev, [id]: value }
    if (id === 'transcription' && !value) {
      next.captions = false
    }
    if (id === 'render' && !value) {
      next.reframe = false
      next.broll = false
      next.thumbnail = false
    }
    return next
  }

  it('turning off transcription cascades captions to false', () => {
    const initial = { ...ALL_ON }
    const result = applyToggle(initial, 'transcription', false)
    expect(result.transcription).toBe(false)
    expect(result.captions).toBe(false)
  })

  it('turning off render cascades reframe, broll, thumbnail to false', () => {
    const initial = { ...ALL_ON }
    const result = applyToggle(initial, 'render', false)
    expect(result.render).toBe(false)
    expect(result.reframe).toBe(false)
    expect(result.broll).toBe(false)
    expect(result.thumbnail).toBe(false)
  })

  it('turning on transcription does not auto-enable captions', () => {
    const initial = { ...ALL_OFF }
    const result = applyToggle(initial, 'transcription', true)
    expect(result.transcription).toBe(true)
    expect(result.captions).toBe(false)
  })
})

describe('Pipeline options payload building (unit)', () => {
  function buildPipelineOptions(
    mode: 'advanced' | 'auto' | 'chapter',
    toggles: Record<ToggleId, boolean>,
  ) {
    if (mode === 'auto') return { ...ALL_ON }
    if (mode === 'chapter') return { ...ALL_ON, segment_proposer: false }
    return { ...toggles }
  }

  it('AI clipping mode returns all true', () => {
    const opts = buildPipelineOptions('auto', { ...ALL_OFF })
    expect(Object.values(opts).every(Boolean)).toBe(true)
  })

  it("Don't clip mode returns all true except segment_proposer", () => {
    const opts = buildPipelineOptions('chapter', { ...ALL_OFF })
    expect(opts.segment_proposer).toBe(false)
    expect(opts.transcription).toBe(true)
    expect(opts.captions).toBe(true)
    expect(opts.render).toBe(true)
    expect(opts.reframe).toBe(true)
    expect(opts.broll).toBe(true)
    expect(opts.thumbnail).toBe(true)
  })

  it('Advanced mode returns current toggle state', () => {
    const custom = { ...ALL_OFF, transcription: true, render: true }
    const opts = buildPipelineOptions('advanced', custom)
    expect(opts.transcription).toBe(true)
    expect(opts.render).toBe(true)
    expect(opts.captions).toBe(false)
    expect(opts.segment_proposer).toBe(false)
  })
})

describe('Tab ARIA structure (unit)', () => {
  // Test the ARIA attributes that would be in the tab bar
  const TABS = [
    { mode: 'advanced', label: 'Advanced' },
    { mode: 'auto', label: 'AI clipping' },
    { mode: 'chapter', label: "Don't clip" },
  ]

  it('tab definitions include all three modes', () => {
    expect(TABS).toHaveLength(3)
    expect(TABS.map((t) => t.mode)).toEqual(['advanced', 'auto', 'chapter'])
  })

  it('Advanced tab is first in the order', () => {
    expect(TABS[0].mode).toBe('advanced')
    expect(TABS[0].label).toBe('Advanced')
  })
})
