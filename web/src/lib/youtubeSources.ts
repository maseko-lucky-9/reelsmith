/** Vision checks in flight at once: the server queues them behind one local model. */
export const MAX_CONCURRENT_CHECKS = 3

export function formatDuration(seconds: number | null): string {
  if (seconds == null) return 'Live'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m)
  return `${h > 0 ? `${h}:` : ''}${mm}:${String(s).padStart(2, '0')}`
}
