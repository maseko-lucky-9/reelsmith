import { useEffect, useState } from 'react'

type HealthStatus = { status: string; job_store: string } | null

export default function App() {
  const [health, setHealth] = useState<HealthStatus>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then(setHealth)
      .catch((e: unknown) => setError(String(e)))
  }, [])

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col items-center justify-center gap-4">
      <h1 className="text-3xl font-bold tracking-tight">Reelsmith</h1>
      <p className="text-zinc-400 text-sm">Phase 0 scaffold — React dashboard coming in Phase 3</p>
      <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-900 px-6 py-4 text-sm font-mono">
        {error && <span className="text-red-400">API error: {error}</span>}
        {!error && !health && <span className="text-zinc-500">Checking API…</span>}
        {health && (
          <span className="text-emerald-400">
            API ✓ &nbsp;|&nbsp; status: {health.status} &nbsp;|&nbsp; store: {health.job_store}
          </span>
        )}
      </div>
    </div>
  )
}
