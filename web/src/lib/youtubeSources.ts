/** Vision checks in flight at once: the server queues them behind one local model. */
export const MAX_CONCURRENT_CHECKS = 3

/** A busy server (429) is retried this many times before the badge gives up. */
export const MAX_BUSY_RETRIES = 3

/** Wait before retry number `attempt` (0-based) of a check the server refused as busy. */
export function busyRetryDelay(attempt: number): number {
  return 3000 * (attempt + 1)
}

/** `apiFetch` throws `Error("<status> <body>")`: true for the server's "too many checks" answer. */
export function isBusyError(error: unknown): boolean {
  return error instanceof Error && error.message.startsWith('429')
}

/** What a badge says when the check could not be made, by the API's cause code. */
export function unavailableHint(cause: string | null | undefined): string {
  switch (cause) {
    case 'stream_blocked':
      return 'YouTube refused to hand out the video stream (its bot check). Search still works.'
    case 'stream_failed':
      return 'Could not read the video stream (live, private or removed?).'
    case 'frames_failed':
      return 'Could not grab enough frames from the stream.'
    case 'ollama_unavailable':
      return 'The local vision model did not answer: is Ollama running with the model pulled?'
    case 'reply_unusable':
    case 'inconclusive':
      return 'The vision model gave no clear answer.'
    case 'timeout':
      return 'The check ran out of time.'
    default:
      return 'The check could not be made.'
  }
}

export function formatDuration(seconds: number | null): string {
  if (seconds == null) return 'Live'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m)
  return `${h > 0 ? `${h}:` : ''}${mm}:${String(s).padStart(2, '0')}`
}
