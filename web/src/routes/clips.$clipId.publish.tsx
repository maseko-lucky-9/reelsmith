/**
 * /clips/$clipId/publish — schedule or immediately publish a clip
 * to a connected social account (W1.14 + TikTok confirmation summary + PUBLISH_* toasts).
 */
import { createRoute, useParams, Link } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { rootRoute } from './root'
import { api } from '@/api/client'
import type { PublishJob } from '@/api/client'

export const clipPublishRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/clips/$clipId/publish',
  component: ClipPublishPage,
})

/**
 * Poll an in-flight publish job and fire sonner toasts on status transitions.
 * Uses react-query's select to detect changes without re-renders.
 */
function usePublishToasts(publishId: string | null) {
  const prevStatusRef = useRef<string | null>(null)

  useQuery({
    queryKey: ['publish-job-watch', publishId],
    queryFn: () => api.getPublish(publishId!),
    enabled: publishId !== null,
    refetchInterval: (query) => {
      const data = query.state.data as PublishJob | undefined
      if (!data) return 2000
      if (['published', 'posted_unverified', 'failed', 'cancelled'].includes(data.status))
        return false
      return 2000
    },
    select(data) {
      const prev = prevStatusRef.current
      const next = data.status
      if (prev !== next) {
        prevStatusRef.current = next
        if (next === 'queued') {
          toast.info('Queued for posting')
        } else if (next === 'posted_unverified') {
          toast.success('Posted! (unverified — check profile)')
        } else if (next === 'published') {
          toast.success('Published successfully!')
        } else if (next === 'failed') {
          toast.error(`Publish failed: ${data.error ?? 'unknown error'}`)
        }
      }
      return data
    },
  })
}

function ClipPublishPage() {
  const { clipId } = useParams({ from: '/clips/$clipId/publish' })
  const queryClient = useQueryClient()

  const accountsQuery = useQuery({
    queryKey: ['social-accounts'],
    queryFn: () => api.listSocialAccounts(),
  })
  const jobsQuery = useQuery({
    queryKey: ['publish-jobs', clipId],
    queryFn: () => api.listPublishForClip(clipId),
    refetchInterval: 3000,
  })
  const clipQuery = useQuery({
    queryKey: ['clip', clipId],
    queryFn: () => api.getClip(clipId),
  })

  const [accountId, setAccountId] = useState<string>('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [hashtagsRaw, setHashtagsRaw] = useState('')
  const [scheduleAt, setScheduleAt] = useState('')

  // Track the most recently submitted publish job ID for toast polling.
  const [activePublishId, setActivePublishId] = useState<string | null>(null)
  usePublishToasts(activePublishId)

  // Hydrate title from clip once it loads (existing behaviour).
  useMemo(() => {
    if (clipQuery.data && !title) setTitle(clipQuery.data.title ?? '')
  }, [clipQuery.data, title])

  // Prefill description from clip.summary on first load.
  const descriptionHydrated = useRef(false)
  useEffect(() => {
    if (clipQuery.data && !descriptionHydrated.current) {
      descriptionHydrated.current = true
      if (clipQuery.data.summary) setDescription(clipQuery.data.summary)
    }
  }, [clipQuery.data])

  // Prefill hashtags from clip.hashtags on first load.
  const hashtagsHydrated = useRef(false)
  useEffect(() => {
    if (clipQuery.data && !hashtagsHydrated.current) {
      hashtagsHydrated.current = true
      const tags = clipQuery.data.hashtags
      if (tags && tags.length > 0) {
        setHashtagsRaw(tags.map((t) => (t.startsWith('#') ? t : `#${t}`)).join(' '))
      }
    }
  }, [clipQuery.data])

  const parsedHashtags = hashtagsRaw
    .split(/[,\s]+/)
    .map((h) => h.replace(/^#/, '').trim())
    .filter(Boolean)

  const selectedAccount = (accountsQuery.data ?? []).find((a) => a.id === accountId)

  const createMutation = useMutation({
    mutationFn: () =>
      api.createPublish({
        clip_id: clipId,
        social_account_id: accountId,
        title: title || undefined,
        description: description || undefined,
        hashtags: parsedHashtags,
        schedule_at: scheduleAt || undefined,
      }),
    onSuccess: (data) => {
      setActivePublishId(data.id)
      void queryClient.invalidateQueries({ queryKey: ['publish-jobs', clipId] })
    },
    onError: (err: Error) => {
      toast.error(`Failed to submit: ${err.message}`)
    },
  })

  const accounts = accountsQuery.data ?? []
  const jobs = jobsQuery.data ?? []

  return (
    <div className="max-w-3xl mx-auto px-6 py-10 text-zinc-200">
      <Link
        to="/clips/$clipId"
        params={{ clipId }}
        className="text-xs text-zinc-500 hover:text-white"
      >
        ← back to clip
      </Link>
      <h1 className="text-2xl font-semibold mt-2 mb-6">Publish clip</h1>

      {accounts.length === 0 ? (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 mb-6">
          <p className="text-sm">
            No social accounts connected.{' '}
            <Link
              to="/settings/social"
              className="underline text-amber-300 hover:text-amber-200"
            >
              Connect one →
            </Link>
          </p>
        </div>
      ) : null}

      <section className="rounded-lg border border-white/10 p-5 mb-8 space-y-3">
        <label className="block">
          <span className="text-xs uppercase tracking-wide text-zinc-400">
            Account
          </span>
          <select
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            className="mt-1 w-full bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm"
          >
            <option value="">Select an account…</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.platform} — {a.account_handle}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="text-xs uppercase tracking-wide text-zinc-400">
            Title
          </span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="mt-1 w-full bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm"
          />
        </label>

        <label className="block">
          <span className="text-xs uppercase tracking-wide text-zinc-400">
            Description
          </span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="mt-1 w-full bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm"
          />
        </label>

        <label className="block">
          <span className="text-xs uppercase tracking-wide text-zinc-400">
            Hashtags (space or comma separated)
          </span>
          <input
            value={hashtagsRaw}
            onChange={(e) => setHashtagsRaw(e.target.value)}
            placeholder="#shorts #funny"
            className="mt-1 w-full bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm"
          />
        </label>

        <label className="block">
          <span className="text-xs uppercase tracking-wide text-zinc-400">
            Schedule (leave empty to publish now)
          </span>
          <input
            type="datetime-local"
            value={scheduleAt}
            onChange={(e) => setScheduleAt(e.target.value)}
            className="mt-1 w-full bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm"
          />
        </label>

        {/* Confirmation summary — shown when an account is selected */}
        {accountId && selectedAccount ? (
          <div className="rounded-md border border-white/10 bg-zinc-900/50 p-4 space-y-1.5 text-sm">
            <p className="text-xs uppercase tracking-wide text-zinc-400 mb-2 font-semibold">
              Review before publishing
            </p>
            <div className="flex gap-2">
              <span className="text-zinc-500 w-20 shrink-0">Platform</span>
              <span className="capitalize">{selectedAccount.platform}</span>
            </div>
            <div className="flex gap-2">
              <span className="text-zinc-500 w-20 shrink-0">Account</span>
              <span>{selectedAccount.account_handle}</span>
            </div>
            <div className="flex gap-2 items-start">
              <span className="text-zinc-500 w-20 shrink-0">Caption</span>
              <span className="text-zinc-300 break-all">
                {description
                  ? description.slice(0, 200) + (description.length > 200 ? '…' : '')
                  : <em className="text-zinc-600 not-italic">none</em>}
              </span>
            </div>
            <div className="flex gap-2">
              <span className="text-zinc-500 w-20 shrink-0">Hashtags</span>
              <span className="text-zinc-300">
                {parsedHashtags.length > 0
                  ? `${parsedHashtags.length} tag${parsedHashtags.length !== 1 ? 's' : ''}`
                  : <em className="text-zinc-600 not-italic">none</em>}
              </span>
            </div>
            {scheduleAt ? (
              <div className="flex gap-2">
                <span className="text-zinc-500 w-20 shrink-0">Scheduled</span>
                <span>{new Date(scheduleAt).toLocaleString()}</span>
              </div>
            ) : null}
          </div>
        ) : null}

        <button
          type="button"
          disabled={!accountId || createMutation.isPending}
          onClick={() => createMutation.mutate()}
          className="text-xs px-3 py-1.5 rounded-md border border-white/20 hover:border-white/40 disabled:opacity-50"
        >
          {createMutation.isPending
            ? 'Submitting…'
            : scheduleAt
              ? 'Schedule publish'
              : 'Publish now'}
        </button>
      </section>

      <section>
        <h2 className="text-sm uppercase tracking-wide text-zinc-400 mb-3">
          History
        </h2>
        {jobs.length === 0 ? (
          <p className="text-sm text-zinc-500">No publish jobs yet.</p>
        ) : (
          <ul className="divide-y divide-white/5">
            {jobs.map((j: PublishJob) => (
              <li key={j.id} className="py-2.5 text-sm flex items-center gap-3">
                <span
                  className={
                    j.status === 'published' || j.status === 'posted_unverified'
                      ? 'text-emerald-400'
                      : j.status === 'failed'
                        ? 'text-red-400'
                        : 'text-zinc-300'
                  }
                >
                  {j.status === 'posted_unverified' ? 'posted (unverified)' : j.status}
                </span>
                <span className="text-zinc-400">— {j.title || '(no title)'}</span>
                {j.external_post_url ? (
                  <a
                    href={j.external_post_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs underline text-zinc-300 hover:text-white"
                  >
                    open
                  </a>
                ) : null}
                {j.error ? (
                  <span
                    title={j.error}
                    className="text-[11px] text-red-400 truncate max-w-[200px]"
                  >
                    {j.error}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
