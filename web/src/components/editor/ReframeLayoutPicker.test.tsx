import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { ReframeLayoutPicker } from './ReframeLayoutPicker'

describe('ReframeLayoutPicker', () => {
  it('renders all aspects + layouts', () => {
    render(
      <ReframeLayoutPicker
        aspect="9:16"
        layout="fullscreen"
        onAspectChange={() => {}}
        onLayoutChange={() => {}}
      />,
    )
    expect(screen.getByTestId('aspect-9-16')).toBeTruthy()
    expect(screen.getByTestId('aspect-1-1')).toBeTruthy()
    expect(screen.getByTestId('aspect-16-9')).toBeTruthy()
    expect(screen.getByTestId('layout-fullscreen')).toBeTruthy()
    expect(screen.getByTestId('layout-split')).toBeTruthy()
    expect(screen.getByTestId('layout-screenshare')).toBeTruthy()
  })

  it('fires the right callback', () => {
    const onAspect = vi.fn()
    const onLayout = vi.fn()
    render(
      <ReframeLayoutPicker
        aspect="9:16"
        layout="fullscreen"
        onAspectChange={onAspect}
        onLayoutChange={onLayout}
      />,
    )
    fireEvent.click(screen.getByTestId('aspect-1-1'))
    expect(onAspect).toHaveBeenCalledWith('1:1')
    fireEvent.click(screen.getByTestId('layout-split'))
    expect(onLayout).toHaveBeenCalledWith('split')
  })
})
