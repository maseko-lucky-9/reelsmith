/**
 * /uploads/new — drag-and-drop MP4 upload page.
 *
 * Accepts .mp4 / .mov / .m4v, calls POST /api/uploads (multipart),
 * then navigates to /jobs/:jobId to watch processing via the SSE timeline.
 */
import { createRoute, useNavigate } from '@tanstack/react-router'
import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { rootRoute } from './root'
import { api } from '@/api/client'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'

export const uploadsNewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/uploads/new',
  component: UploadNewPage,
})

const ACCEPTED_MIME = ['video/mp4', 'video/quicktime', 'video/x-m4v']
const ACCEPTED_EXT = '.mp4,.mov,.m4v'

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function UploadNewPage() {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)

  function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return
    const picked = files[0]
    if (!ACCEPTED_MIME.includes(picked.type)) {
      toast.error(`Unsupported file type: ${picked.type || picked.name}. Use MP4, MOV or M4V.`)
      return
    }
    setFile(picked)
  }

  function onDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragging(true)
  }

  function onDragLeave() {
    setDragging(false)
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragging(false)
    handleFiles(e.dataTransfer.files)
  }

  async function handleSubmit() {
    if (!file) return
    setUploading(true)
    try {
      const res = await api.uploadVideo(file)
      toast.success('Upload queued — watching progress…')
      void navigate({ to: '/jobs/$jobId', params: { jobId: res.job_id } })
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="max-w-lg space-y-6">
      <h1 className="text-2xl font-bold">Upload video</h1>
      <p className="text-xs text-zinc-500">
        Upload an MP4, MOV or M4V file directly. The pipeline will process it
        the same way as a URL job — transcription, captions, and reels extraction.
      </p>

      <div className="space-y-4">
        <div className="space-y-1">
          <Label>Video file</Label>
          <div
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
            className={[
              'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-10 cursor-pointer transition-colors',
              dragging
                ? 'border-zinc-400 bg-zinc-800/60'
                : 'border-zinc-700 bg-zinc-900 hover:border-zinc-500',
            ].join(' ')}
          >
            {file ? (
              <>
                <p className="text-sm font-medium text-zinc-100">{file.name}</p>
                <p className="text-xs text-zinc-400">{formatBytes(file.size)}</p>
                <p className="text-[11px] text-zinc-500">Click or drag to replace</p>
              </>
            ) : (
              <>
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="h-10 w-10 text-zinc-600"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={1.5}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
                  />
                </svg>
                <p className="text-sm text-zinc-400">
                  Drag and drop, or{' '}
                  <span className="text-zinc-100 underline">browse</span>
                </p>
                <p className="text-xs text-zinc-500">MP4, MOV, M4V</p>
              </>
            )}
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED_EXT}
            className="hidden"
            onChange={(e) => handleFiles(e.target.files)}
          />
        </div>

        <Button
          className="w-full"
          disabled={!file || uploading}
          onClick={() => void handleSubmit()}
        >
          {uploading ? 'Uploading…' : 'Upload and process'}
        </Button>
      </div>
    </div>
  )
}
