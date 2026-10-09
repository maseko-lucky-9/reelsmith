/**
 * The `detail` string of a FastAPI error that `apiFetch` rethrew as
 * `Error("<status> <json body>")`, or null when there is none.
 */
export function apiErrorDetail(err: unknown): string | null {
  if (!(err instanceof Error)) return null
  const body = err.message.replace(/^\d{3}\s/, '')
  try {
    const detail: unknown = (JSON.parse(body) as { detail?: unknown }).detail
    return typeof detail === 'string' ? detail : null
  } catch {
    return null
  }
}
