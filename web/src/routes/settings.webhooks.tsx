/** /settings/webhooks — outbound webhooks (W3.12). */
import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './root'

export const webhooksSettingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/webhooks',
  component: WebhooksPage,
})

function WebhooksPage() {
  return (
    <div className="max-w-3xl mx-auto px-6 py-10 text-zinc-200">
      <h1 className="text-2xl font-semibold mb-6">Webhooks</h1>
      <p className="text-sm text-zinc-400 mb-4">
        POST URLs to receive events as JSON with an HMAC-SHA256
        signature header (<code>X-ReelSmith-Signature</code>).
      </p>
      <ul className="text-xs text-zinc-500 list-disc pl-5 space-y-1">
        <li>Subscribed events: <code>clip.published</code>, <code>clip.failed</code>, <code>job.completed</code>, or <code>*</code> for all.</li>
        <li>Retry policy: 3 attempts on 5xx; 4xx is non-retryable.</li>
        <li>Verify signature on receipt:{' '}
          <code className="font-mono">hmac(secret, body, sha256)</code>.
        </li>
      </ul>
      <p className="mt-6 text-xs text-zinc-500">
        Web UI for create/list/delete arrives in a follow-up. Wire via
        the API today:{' '}
        <code className="font-mono">POST /api/webhooks</code> (W3.5).
      </p>
    </div>
  )
}
