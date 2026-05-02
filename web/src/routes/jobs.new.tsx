import { createRoute, useNavigate } from '@tanstack/react-router'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { rootRoute } from './root'
import { api } from '@/api/client'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'

export const jobsNewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/jobs/new',
  component: NewJobPage,
})

function NewJobPage() {
  const navigate = useNavigate()
  const [url, setUrl] = useState('')
  const [downloadPath, setDownloadPath] = useState('/tmp/yt')
  const [language, setLanguage] = useState('en-US')
  const [segmentMode, setSegmentMode] = useState<'auto' | 'chapter'>('chapter')

  const mutation = useMutation({
    mutationFn: () =>
      api.createJob({
        url,
        download_path: downloadPath,
        language,
        segment_mode: segmentMode,
      }),
    onSuccess: (data) => {
      toast.success(`Job ${data.job_id} queued`)
      void navigate({ to: '/jobs/$jobId', params: { jobId: data.job_id } })
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  return (
    <div className="max-w-lg space-y-6">
      <h1 className="text-2xl font-bold">New Job</h1>
      <p className="text-xs text-zinc-500">
        Note: Only process videos you have the right to use. Check the platform's
        terms of service before proceeding.
      </p>

      <div className="space-y-4">
        <div className="space-y-1">
          <Label>Video URL</Label>
          <Input
            placeholder="https://youtube.com/watch?v=..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="bg-zinc-900 border-zinc-700"
          />
        </div>

        <div className="space-y-1">
          <Label>Download path</Label>
          <Input
            value={downloadPath}
            onChange={(e) => setDownloadPath(e.target.value)}
            className="bg-zinc-900 border-zinc-700"
          />
        </div>

        <div className="space-y-1">
          <Label>Language</Label>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
          >
            <option value="en-US">English (US)</option>
            <option value="en-GB">English (UK)</option>
            <option value="es">Spanish</option>
            <option value="fr">French</option>
            <option value="de">German</option>
            <option value="pt">Portuguese</option>
          </select>
        </div>

        <div className="space-y-1">
          <Label>Segment mode</Label>
          <select
            value={segmentMode}
            onChange={(e) => setSegmentMode(e.target.value as 'auto' | 'chapter')}
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
          >
            <option value="chapter">Chapter mode (YouTube chapters)</option>
            <option value="auto">Auto (heuristic scoring)</option>
          </select>
        </div>

        <Button
          className="w-full"
          disabled={!url || mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? 'Queuing…' : 'Start job'}
        </Button>
      </div>
    </div>
  )
}
