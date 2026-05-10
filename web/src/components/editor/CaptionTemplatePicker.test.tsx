import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import {
  CAPTION_TEMPLATES,
  CaptionTemplatePicker,
} from './CaptionTemplatePicker'

describe('CaptionTemplatePicker', () => {
  it('renders 6 templates including the 5 animated presets', () => {
    render(<CaptionTemplatePicker value="static" onChange={() => {}} />)
    expect(CAPTION_TEMPLATES.map((t) => t.name)).toContain('hormozi')
    expect(CAPTION_TEMPLATES.map((t) => t.name)).toContain('mrbeast')
    for (const t of CAPTION_TEMPLATES) {
      expect(screen.getByTestId(`caption-template-${t.name}`)).toBeTruthy()
    }
  })

  it('marks the currently-selected template', () => {
    render(<CaptionTemplatePicker value="hormozi" onChange={() => {}} />)
    expect(
      screen.getByTestId('caption-template-hormozi').dataset.selected,
    ).toBe('true')
    expect(
      screen.getByTestId('caption-template-mrbeast').dataset.selected,
    ).toBe('false')
  })

  it('fires onChange when a card is clicked', () => {
    const onChange = vi.fn()
    render(<CaptionTemplatePicker value="static" onChange={onChange} />)
    fireEvent.click(screen.getByTestId('caption-template-mrbeast'))
    expect(onChange).toHaveBeenCalledWith('mrbeast')
  })
})
