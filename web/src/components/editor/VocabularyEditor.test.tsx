import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { VocabularyEditor } from './VocabularyEditor'

describe('VocabularyEditor', () => {
  it('renders empty state', () => {
    render(<VocabularyEditor vocabulary={{}} onChange={() => {}} />)
    expect(
      screen.getByText(/no replacements/i),
    ).toBeTruthy()
  })

  it('lists existing entries', () => {
    render(
      <VocabularyEditor
        vocabulary={{ OpusClip: 'ReelSmith', ai: 'AI' }}
        onChange={() => {}}
      />,
    )
    expect(screen.getByTestId('vocab-entry-OpusClip')).toBeTruthy()
    expect(screen.getByTestId('vocab-entry-ai')).toBeTruthy()
  })

  it('add button calls onChange with new entry', () => {
    const onChange = vi.fn()
    render(<VocabularyEditor vocabulary={{}} onChange={onChange} />)
    fireEvent.change(screen.getByPlaceholderText('Source word'), {
      target: { value: 'foo' },
    })
    fireEvent.change(screen.getByPlaceholderText('Replacement'), {
      target: { value: 'bar' },
    })
    fireEvent.click(screen.getByTestId('vocab-add'))
    expect(onChange).toHaveBeenCalledWith({ foo: 'bar' })
  })
})
