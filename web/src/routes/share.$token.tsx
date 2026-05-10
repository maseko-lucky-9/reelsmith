/** /share/$token — public read-only clip view (W3.13). */
import { createRoute, useParams } from '@tanstack/react-router'
import { rootRoute } from './root'

export const shareRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/share/$token',
  component: SharePage,
})

function SharePage() {
  const { token } = useParams({ from: '/share/$token' })

  return (
    <div className="min-h-screen flex items-center justify-center p-6 bg-zinc-950 text-zinc-100">
      <div className="max-w-2xl w-full rounded-lg border border-white/10 p-8">
        <h1 className="text-xl font-semibold mb-2">Shared clip</h1>
        <p className="text-sm text-zinc-400 mb-4">
          Public preview backed by an HMAC-signed share link. The token
          embeds the clip id and expiry; the server checks revocation
          status before resolving.
        </p>
        <p className="text-xs font-mono text-zinc-500 break-all">
          {token}
        </p>
        <p className="mt-4 text-xs text-zinc-500">
          The video player UI lands in a follow-up — this route exists
          today so the share link contract is reachable.
        </p>
      </div>
    </div>
  )
}
