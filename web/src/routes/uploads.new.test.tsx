import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { createElement, type ComponentType } from 'react'

vi.mock('@/api/client', () => ({
  api: { uploadVideo: vi.fn() },
}))

vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => vi.fn(),
}))

import { api } from '@/api/client'
import { uploadsNewRoute } from './uploads.new'

function renderPage() {
  const Page = uploadsNewRoute.options.component as ComponentType
  const view = render(createElement(Page))
  const input = view.container.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File([new Uint8Array(8)], 'talk.mp4', { type: 'video/mp4' })
  fireEvent.change(input, { target: { files: [file] } })
  return file
}

beforeEach(() => {
  vi.mocked(api.uploadVideo).mockReset()
  vi.mocked(api.uploadVideo).mockResolvedValue({ job_id: 'job-1', upload_path: '/u/x.mp4', status: 'queued' })
})

describe('UploadNewPage — prompt (spec 014)', () => {
  it('sends the prompt with the upload', async () => {
    const file = renderPage()
    fireEvent.change(screen.getByLabelText('Find moments (optional)'), {
      target: { value: '  every shot of a red car  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Upload and process' }))

    await waitFor(() => expect(api.uploadVideo).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.uploadVideo).mock.calls[0]).toEqual([file, 'every shot of a red car'])
  })

  it('sends no prompt when the box is blank', async () => {
    renderPage()
    fireEvent.change(screen.getByLabelText('Find moments (optional)'), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Upload and process' }))

    await waitFor(() => expect(api.uploadVideo).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.uploadVideo).mock.calls[0][1]).toBeUndefined()
  })

  it('explains what the prompt does', () => {
    renderPage()
    expect(screen.getByText(/overrides the video’s chapters/)).toBeTruthy()
  })
})
