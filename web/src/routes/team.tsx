/** /team — workspace + members (W3.11). */
import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './root'

export const teamRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/team',
  component: TeamPage,
})

function TeamPage() {
  return (
    <div className="max-w-3xl mx-auto px-6 py-10 text-zinc-200">
      <h1 className="text-2xl font-semibold mb-6">Team</h1>
      <p className="text-sm text-zinc-400">
        Single-tenant mode — only the local workspace is active.
        Workspace members and role management arrive when{' '}
        <code className="font-mono">YTVIDEO_AUTH_ENABLED=true</code>.
      </p>
      <ul className="mt-6 space-y-2 text-sm">
        <li className="flex justify-between border-b border-white/5 py-2">
          <span>local</span>
          <span className="text-zinc-500">owner</span>
        </li>
      </ul>
    </div>
  )
}
