/**
 * /clips/$clipId/publish — schedule or immediately publish a clip
 * to a connected social account (W1.14).
 */
import { createRoute, useParams, Link } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { rootRoute } from './root'
import { api } from '@/api/client'
import type { PublishJob } from '@/api/client'

export const clipPublishRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/clips/$clipId/publish',
  component: ClipPublishPage,
})

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

  // Hydrate defaults from the clip once it loads.
  useMemo(() => {
    if (clipQuery.data && !title)
      setTitle(clipQuery.data.title ?? '')
  }, [clipQuery.data, title])

  const createMutation = useMutation({
    mutationFn: () =>
      api.createPublish({
        clip_id: clipId,
        social_account_id: accountId,
        title: title || undefined,
        description: description || undefined,
        hashtags: hashtagsRaw
          .split(/[,\s]+/)
          .map((h) => h.replace(/^#/, '').trim())
          .filter(Boolean),
        schedule_at: scheduleAt || undefined,
      }),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['publish-jobs', clipId] }),
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
                    j.status === 'published'
                      ? 'text-emerald-400'
                      : j.status === 'failed'
                        ? 'text-red-400'
                        : 'text-zinc-300'
                  }
                >
                  {j.status}
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
