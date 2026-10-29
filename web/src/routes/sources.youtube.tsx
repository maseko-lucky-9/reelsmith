import { createRoute, useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { Captions, Check, CircleHelp, Loader2, Search } from 'lucide-react'
import { rootRoute } from './root'
import { api } from '@/api/client'
import type { SourceCleanCheck, YoutubeSearchItem } from '@/api/client'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  MAX_BUSY_RETRIES,
  MAX_CONCURRENT_CHECKS,
  busyRetryDelay,
  formatDuration,
  isBusyError,
  unavailableHint,
} from '@/lib/youtubeSources'

export const youtubeSourcesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/sources/youtube',
  component: YoutubeSourcesPage,
})

/** Results per search. */
const RESULT_LIMIT = 12

function YoutubeSourcesPage() {
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [hideCaptioned, setHideCaptioned] = useState(true)
  // Videos whose check has finished (answered or failed); the rest wait in order for a slot.
  const [settledIds, setSettledIds] = useState<ReadonlySet<string>>(new Set())
  // Videos the user found to have captions, reported by their cards.
  const [captionedIds, setCaptionedIds] = useState<ReadonlySet<string>>(new Set())
  // Videos whose check the user asked to run again, oldest request first: they queue behind the rest.
  const [retryOrder, setRetryOrder] = useState<readonly string[]>([])

  const search = useQuery({
    queryKey: ['youtube-search', submitted],
    queryFn: ({ signal }) => api.searchYoutube(submitted, RESULT_LIMIT, signal),
    enabled: submitted !== '',
    staleTime: 5 * 60_000,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const queryClient = useQueryClient()

  const items = search.data?.items ?? []
  // The queue: results in order, videos the user asked to retry after all of them, in click order.
  const queue = [
    ...items.filter((i) => !retryOrder.includes(i.id)),
    ...retryOrder.flatMap((id) => items.filter((i) => i.id === id)),
  ]
  // The first MAX_CONCURRENT_CHECKS videos still without an answer may run their check.
  const admitted = new Set(queue.filter((i) => !settledIds.has(i.id)).slice(0, MAX_CONCURRENT_CHECKS).map((i) => i.id))
  const hidden = hideCaptioned ? items.filter((i) => captionedIds.has(i.id)) : []
  const shown = items.filter((i) => !hidden.includes(i))

  const markSettled = useCallback((id: string, captioned: boolean) => {
    setSettledIds((prev) => (prev.has(id) ? prev : new Set(prev).add(id)))
    setCaptionedIds((prev) => {
      if (prev.has(id) === captioned) return prev
      const next = new Set(prev)
      if (captioned) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])

  // A retry goes through the same queue as a first check: the video leaves the settled set and
  // joins the end of the queue, its query starts over, and it runs once it is among the first
  // MAX_CONCURRENT_CHECKS without an answer.
  const retryCheck = useCallback(
    (id: string) => {
      setRetryOrder((prev) => [...prev.filter((x) => x !== id), id])
      setSettledIds((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
      void queryClient.resetQueries({ queryKey: ['youtube-clean', id] })
    },
    [queryClient],
  )

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = text.trim()
    if (trimmed) setSubmitted(trimmed)
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold">Search YouTube</h1>
        <p className="text-sm text-zinc-400">
          Find a source video. Each result is checked for burned-in captions (Reelsmith adds its own) by a local vision
          model, a few at a time.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2" role="search">
        <div className="relative flex-1 flex items-center">
          <Search className="absolute left-3.5 w-4 h-4 text-zinc-400 pointer-events-none" />
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={200}
            placeholder="Search YouTube, e.g. ted talk sleep"
            aria-label="Search YouTube"
            className="pl-10 h-10 rounded-xl bg-[var(--card-bg)] border-white/10 text-white placeholder:text-zinc-500"
          />
        </div>
        <button
          type="submit"
          disabled={text.trim() === ''}
          className="px-4 h-10 rounded-xl bg-white text-black font-semibold text-sm hover:bg-zinc-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Search
        </button>
      </form>

      <label className="inline-flex items-center gap-2 text-sm text-zinc-300 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={hideCaptioned}
          onChange={(e) => setHideCaptioned(e.target.checked)}
          className="h-4 w-4 accent-white"
        />
        Hide videos with captions
        {hidden.length > 0 && <span className="text-xs text-zinc-500">({hidden.length} hidden)</span>}
      </label>

      {search.isFetching && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4" aria-label="Searching">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="aspect-video rounded-lg" />
          ))}
        </div>
      )}

      {search.isError && (
        <p role="alert" className="text-sm text-red-400">
          Search failed: {search.error.message}
        </p>
      )}

      {!search.isFetching && search.isSuccess && items.length === 0 && (
        <p className="text-sm text-zinc-400">No videos found for “{submitted}”.</p>
      )}

      {!search.isFetching && items.length > 0 && (
        <>
          {shown.length === 0 && (
            <p className="text-sm text-zinc-400">Every result so far has captions. Turn off the filter to see them.</p>
          )}
          <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {items.map((item) => (
              <SourceCard
                key={item.id}
                item={item}
                admitted={admitted.has(item.id)}
                hidden={hidden.includes(item)}
                onSettled={markSettled}
                onRetry={() => retryCheck(item.id)}
                onUse={() => void navigate({ to: '/workflow', search: { url: item.url } })}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function SourceCard({
  item,
  admitted,
  hidden,
  onSettled,
  onRetry,
  onUse,
}: {
  item: YoutubeSearchItem
  admitted: boolean
  hidden: boolean
  onSettled: (id: string, captioned: boolean) => void
  onRetry: () => void
  onUse: () => void
}) {
  const check = useQuery<SourceCleanCheck>({
    queryKey: ['youtube-clean', item.id],
    // The signal cancels the request when the card goes away (a new search, leaving the page).
    queryFn: ({ signal }) => api.checkYoutubeClean(item.id, signal),
    enabled: admitted,
    // One answer per video for the session: no polling, no refetch on focus.
    staleTime: Infinity,
    gcTime: 60 * 60_000,
    // Only "the server is busy" (429) is tried again, a few times, with a growing pause; the video
    // keeps its slot meanwhile. Anything else is an answer: unchecked.
    retry: (failureCount, error) => isBusyError(error) && failureCount < MAX_BUSY_RETRIES,
    retryDelay: (attempt) => busyRetryDelay(attempt),
    refetchOnWindowFocus: false,
  })
  const settled = check.isSuccess || check.isError
  const captioned = check.data?.clean === false

  // Report the answer up so the next video in line gets the slot and the filter can hide this one.
  useEffect(() => {
    if (settled) onSettled(item.id, captioned)
  }, [settled, captioned, item.id, onSettled])

  if (hidden) return null

  return (
    <li
      className="rounded-lg overflow-hidden ring-1 ring-white/8 flex flex-col"
      style={{ background: 'var(--card-bg)' }}
      data-testid="source-card"
    >
      <div className="relative aspect-video bg-zinc-900">
        <img src={item.thumbnail_url} alt="" loading="lazy" className="w-full h-full object-cover" />
        <span className="absolute bottom-2 right-2 text-[11px] px-1.5 py-0.5 rounded bg-black/70 text-white">
          {formatDuration(item.duration_seconds)}
        </span>
        <div className="absolute top-2 left-2">
          <CleanBadge check={check.data} pending={!settled} waiting={!admitted} onRetry={onRetry} />
        </div>
      </div>
      <div className="p-3 flex flex-col gap-2 flex-1">
        <h2 className="text-sm font-medium text-white line-clamp-2" title={item.title}>
          {item.title}
        </h2>
        <p className="text-xs text-zinc-400 truncate">{item.channel}</p>
        <button
          type="button"
          onClick={onUse}
          className="mt-auto w-full py-2 rounded-lg bg-white text-black text-sm font-semibold hover:bg-zinc-100 transition-colors"
        >
          Use this video
        </button>
      </div>
    </li>
  )
}

function CleanBadge({
  check,
  pending,
  waiting,
  onRetry,
}: {
  check: SourceCleanCheck | undefined
  pending: boolean
  waiting: boolean
  onRetry: () => void
}) {
  const base = 'inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded font-medium'
  if (pending) {
    return (
      <span className={`${base} bg-black/70 text-zinc-200`} data-testid="clean-badge" data-state="pending">
        <Loader2 className={`w-3 h-3 ${waiting ? '' : 'animate-spin'}`} />
        {waiting ? 'Waiting…' : 'Checking…'}
      </span>
    )
  }
  if (check?.clean === true) {
    return (
      <span
        className={`${base} bg-emerald-600 text-white`}
        data-testid="clean-badge"
        data-state="clean"
        title={check.reason}
      >
        <Check className="w-3 h-3" />
        Clean
      </span>
    )
  }
  if (check?.clean === false) {
    return (
      <span
        className={`${base} bg-amber-600 text-white`}
        data-testid="clean-badge"
        data-state="captions"
        title={check.reason}
      >
        <Captions className="w-3 h-3" />
        Has captions
      </span>
    )
  }
  return (
    <button
      type="button"
      onClick={onRetry}
      className={`${base} bg-black/70 text-zinc-300 hover:text-white`}
      data-testid="clean-badge"
      data-state="unknown"
      title={`${unavailableHint(check?.cause)} Click to retry.`}
    >
      <CircleHelp className="w-3 h-3" />
      Unchecked
    </button>
  )
}
