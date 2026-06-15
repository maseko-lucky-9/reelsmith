/**
 * /generate/new — AI generation mode.
 *
 * Submits a brief (title, script, shots, optional voice profile + music URL) to
 * POST /api/generate, then navigates to /jobs/:jobId to watch progress via the
 * same SSE timeline used by URL/upload jobs (generate jobs emit identical events).
 *
 * Mirrors routes/jobs.new.tsx: controlled useState inputs (no react-hook-form/zod),
 * useMutation → api.postGenerate, onSuccess toast + navigate, onError toast.
 */
import { createRoute, useNavigate } from '@tanstack/react-router'
import { useMutation } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { rootRoute } from './root'
import { api, type GenerateShot } from '@/api/client'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'

export const generateNewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/generate/new',
  component: GenerateNewPage,
})

/** Parse a multiline textarea into shots: one prompt per non-empty line. */
function parseShots(shotsText: string, defaultSeconds: number): GenerateShot[] {
  return shotsText
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((prompt) => ({ prompt, seconds: defaultSeconds }))
}

function GenerateNewPage() {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [script, setScript] = useState('')
  const [shotsText, setShotsText] = useState('')
  const [defaultSeconds, setDefaultSeconds] = useState(5)
  const [voiceProfile, setVoiceProfile] = useState('')
  const [musicUrl, setMusicUrl] = useState('')

  const shots = useMemo(
    () => parseShots(shotsText, defaultSeconds),
    [shotsText, defaultSeconds],
  )

  const canSubmit = title.trim().length > 0 && script.trim().length > 0

  const mutation = useMutation({
    mutationFn: () =>
      api.postGenerate({
        title: title.trim(),
        script: script.trim(),
        shots,
        voice_profile: voiceProfile.trim() || undefined,
        music_url: musicUrl.trim() || undefined,
      }),
    onSuccess: (data) => {
      toast.success(`Generation ${data.job_id} queued`)
      void navigate({ to: '/jobs/$jobId', params: { jobId: data.job_id } })
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  return (
    <div className="max-w-lg space-y-6">
      <h1 className="text-2xl font-bold">Generate</h1>
      <p className="text-xs text-zinc-500">
        Describe what you want and let the pipeline generate it. Enter a title, a
        script, and one shot prompt per line. The job is processed the same way as
        a URL job — you'll watch progress on the next screen.
      </p>

      <div className="space-y-4">
        <div className="space-y-1">
          <Label>Title</Label>
          <Input
            placeholder="A short title for this generation"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="bg-zinc-900 border-zinc-700"
          />
        </div>

        <div className="space-y-1">
          <Label>Script</Label>
          <textarea
            placeholder="The narration / voiceover script…"
            value={script}
            onChange={(e) => setScript(e.target.value)}
            rows={5}
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-white/20"
          />
        </div>

        <div className="space-y-1">
          <Label>Shots (one prompt per line)</Label>
          <textarea
            placeholder={'A sunrise over mountains\nClose-up of a coffee cup\nCity street at night'}
            value={shotsText}
            onChange={(e) => setShotsText(e.target.value)}
            rows={4}
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-white/20"
          />
          <p className="text-xs text-zinc-500">
            {shots.length} shot{shots.length === 1 ? '' : 's'} parsed.
          </p>
        </div>

        <div className="space-y-1">
          <Label>Seconds per shot</Label>
          <Input
            type="number"
            min={1}
            max={60}
            value={defaultSeconds}
            onChange={(e) =>
              setDefaultSeconds(Math.max(1, Number(e.target.value) || 1))
            }
            className="bg-zinc-900 border-zinc-700"
          />
        </div>

        <div className="space-y-1">
          <Label>Voice profile (optional)</Label>
          <Input
            placeholder="e.g. narrator-male, narrator-female"
            value={voiceProfile}
            onChange={(e) => setVoiceProfile(e.target.value)}
            className="bg-zinc-900 border-zinc-700"
          />
        </div>

        <div className="space-y-1">
          <Label>Music URL (optional)</Label>
          <Input
            placeholder="https://…/track.mp3"
            value={musicUrl}
            onChange={(e) => setMusicUrl(e.target.value)}
            className="bg-zinc-900 border-zinc-700"
          />
        </div>

        <Button
          className="w-full"
          disabled={!canSubmit || mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? 'Queuing…' : 'Generate'}
        </Button>
      </div>
    </div>
  )
}
