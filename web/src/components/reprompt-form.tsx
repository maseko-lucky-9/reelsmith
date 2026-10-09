import { useState, type FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api, type RepromptRequest } from '@/api/client'
import { apiErrorDetail } from '@/lib/apiErrorDetail'

interface Props {
  jobId: string
  /** A reprompt of this job is running; the form stays disabled until it ends. */
  busy: boolean
  /** Called once the backend has queued the reprompt (202). */
  onQueued: () => void
}

function seconds(value: string): number | undefined {
  if (value.trim() === '') return undefined
  const n = Number(value)
  return Number.isFinite(n) ? n : undefined
}

/**
 * Re-discovers a completed job's clips with a new prompt and clip length range
 * (POST /jobs/{id}/reprompt). The new clips replace the current ones only once all
 * of them have rendered; a refused request (409) shows the server's reason.
 */
export function RepromptForm({ jobId, busy, onQueued }: Props) {
  const [prompt, setPrompt] = useState('')
  const [minSeconds, setMinSeconds] = useState('')
  const [maxSeconds, setMaxSeconds] = useState('')

  const mutation = useMutation({
    mutationFn: (body: RepromptRequest) => api.repromptJob(jobId, body),
    onSuccess: () => {
      toast.success('Reprompt queued; the new clips replace these when they are ready')
      onQueued()
    },
    onError: (err) => {
      const detail = apiErrorDetail(err)
      toast.error(detail ? `Cannot reprompt: ${detail}` : 'Failed to queue the reprompt')
    },
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    const body: RepromptRequest = {}
    const text = prompt.trim()
    if (text) body.prompt = text
    const min = seconds(minSeconds)
    const max = seconds(maxSeconds)
    if (min !== undefined) body.length_min_seconds = min
    if (max !== undefined) body.length_max_seconds = max
    mutation.mutate(body)
  }

  const disabled = busy || mutation.isPending
  const inputClass =
    'rounded-lg bg-[var(--card-bg)] border border-white/10 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-white/20'

  return (
    <form onSubmit={submit} className="space-y-2 rounded-xl border border-white/10 p-3">
      <label className="block text-xs text-zinc-400" htmlFor="reprompt-prompt">
        New prompt
      </label>
      <textarea
        id="reprompt-prompt"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        maxLength={2000}
        rows={2}
        placeholder="e.g. the moments about gender equality"
        className={`w-full px-3 py-1.5 ${inputClass}`}
      />
      <div className="flex items-end gap-3 flex-wrap">
        <label className="flex flex-col gap-1 text-xs text-zinc-400">
          Min length (s)
          <input
            type="number"
            min={0}
            max={3600}
            value={minSeconds}
            onChange={(e) => setMinSeconds(e.target.value)}
            className={`w-24 px-2 py-1 ${inputClass}`}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-zinc-400">
          Max length (s)
          <input
            type="number"
            min={0}
            max={3600}
            value={maxSeconds}
            onChange={(e) => setMaxSeconds(e.target.value)}
            className={`w-24 px-2 py-1 ${inputClass}`}
          />
        </label>
        <button
          type="submit"
          disabled={disabled}
          className="px-3 py-1.5 rounded-lg bg-white text-black text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {busy ? 'Reprompting…' : 'Reprompt'}
        </button>
      </div>
    </form>
  )
}
