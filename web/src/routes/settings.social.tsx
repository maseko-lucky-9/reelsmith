/**
 * /settings/social — manage social accounts (W1.14 + TikTok cookie connect).
 *
 * TikTok platform uses cookie-JSON auth; all other platforms keep the original
 * access-token form. TikTok accounts show a days-remaining expiry badge and a
 * capabilities warning when YTVIDEO_OAUTH_ENCRYPT_KEY is absent.
 */
import { createRoute } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { rootRoute } from './root'
import { api } from '@/api/client'
import type { SocialAccount, TikTokCapabilities } from '@/api/client'

export const socialAccountsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/social',
  component: SocialAccountsPage,
})

const PLATFORMS = [
  { value: 'youtube', label: 'YouTube' },
  { value: 'tiktok', label: 'TikTok' },
  { value: 'instagram', label: 'Instagram' },
  { value: 'linkedin', label: 'LinkedIn' },
  { value: 'x', label: 'X (Twitter)' },
] as const

/** Days remaining until expires_at; null if no expiry is set. */
function daysUntil(expiresAt: string | null): number | null {
  if (!expiresAt) return null
  const diff = new Date(expiresAt).getTime() - Date.now()
  return Math.floor(diff / (1000 * 60 * 60 * 24))
}

function ExpiryBadge({ expiresAt }: { expiresAt: string | null }) {
  const days = daysUntil(expiresAt)
  if (days === null) return null
  if (days < 0) {
    return (
      <span className="ml-2 inline-block text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-400 border border-red-500/30">
        Expired
      </span>
    )
  }
  return (
    <span className="ml-2 inline-block text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">
      Re-connect in {days}d
    </span>
  )
}

function SocialAccountsPage() {
  const queryClient = useQueryClient()
  const accountsQuery = useQuery({
    queryKey: ['social-accounts'],
    queryFn: () => api.listSocialAccounts(),
  })

  const [platform, setPlatform] = useState<SocialAccount['platform']>('youtube')

  // ── Generic (non-TikTok) form fields ────────────────────────────────────
  const [handle, setHandle] = useState('')
  const [token, setToken] = useState('')

  // ── TikTok-specific form fields ──────────────────────────────────────────
  const [tikHandle, setTikHandle] = useState('')
  const [tikCookiesJson, setTikCookiesJson] = useState('')
  const [tikDisplayName, setTikDisplayName] = useState('')

  const [error, setError] = useState<string | null>(null)

  // Fetch TikTok capabilities when that platform is selected.
  const capabilitiesQuery = useQuery<TikTokCapabilities>({
    queryKey: ['tiktok-capabilities'],
    queryFn: () => api.getTikTokCapabilities(),
    enabled: platform === 'tiktok',
    retry: false,
  })

  // Reset form fields when platform changes.
  useEffect(() => {
    setHandle('')
    setToken('')
    setTikHandle('')
    setTikCookiesJson('')
    setTikDisplayName('')
    setError(null)
  }, [platform])

  // ── Generic create mutation ──────────────────────────────────────────────
  const createMutation = useMutation({
    mutationFn: () =>
      api.createSocialAccount({
        platform,
        account_handle: handle,
        access_token: token,
      }),
    onSuccess: () => {
      setHandle('')
      setToken('')
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['social-accounts'] })
    },
    onError: (e: unknown) => setError(String(e)),
  })

  // ── TikTok connect mutation ──────────────────────────────────────────────
  const tikConnectMutation = useMutation({
    mutationFn: () =>
      api.connectTikTok({
        account_handle: tikHandle,
        cookies_json: tikCookiesJson,
        display_name: tikDisplayName || undefined,
      }),
    onSuccess: () => {
      setTikHandle('')
      setTikCookiesJson('')
      setTikDisplayName('')
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['social-accounts'] })
    },
    onError: (e: unknown) => setError(String(e)),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteSocialAccount(id),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['social-accounts'] }),
  })

  const isTikTok = platform === 'tiktok'
  const caps = capabilitiesQuery.data

  return (
    <div className="max-w-3xl mx-auto px-6 py-10 text-zinc-200">
      <h1 className="text-2xl font-semibold mb-6">Social accounts</h1>

      <section className="rounded-lg border border-white/10 p-5 mb-8">
        <h2 className="text-sm uppercase tracking-wide text-zinc-400 mb-4">
          Connect a new account
        </h2>

        {/* Platform selector — always visible */}
        <div className="mb-4">
          <select
            value={platform}
            onChange={(e) =>
              setPlatform(e.target.value as SocialAccount['platform'])
            }
            className="bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm w-full md:w-auto"
          >
            {PLATFORMS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>

        {/* TikTok-specific capability warning */}
        {isTikTok && caps && !caps.has_stable_key && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 mb-4">
            <p className="text-xs text-amber-300">
              Set{' '}
              <code className="font-mono bg-zinc-800 px-1 rounded">
                YTVIDEO_OAUTH_ENCRYPT_KEY
              </code>{' '}
              in{' '}
              <code className="font-mono bg-zinc-800 px-1 rounded">.env</code>{' '}
              or cookies will not persist after restart.
            </p>
          </div>
        )}

        {isTikTok ? (
          /* ── TikTok cookie-JSON form ──────────────────────────────────── */
          <div className="space-y-3">
            <div>
              <label className="block text-xs uppercase tracking-wide text-zinc-400 mb-1">
                Account handle
              </label>
              <input
                placeholder="@reelsmith"
                value={tikHandle}
                onChange={(e) => setTikHandle(e.target.value)}
                className="w-full bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs uppercase tracking-wide text-zinc-400 mb-1">
                Cookie JSON export
              </label>
              <p className="text-[11px] text-zinc-500 mb-1">
                Paste the full JSON array from DevTools → Application → Cookies
              </p>
              <textarea
                value={tikCookiesJson}
                onChange={(e) => setTikCookiesJson(e.target.value)}
                rows={5}
                placeholder='[{"name":"sessionid","value":"...","domain":".tiktok.com",...}]'
                className="w-full bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm font-mono text-xs"
              />
            </div>
            <div>
              <label className="block text-xs uppercase tracking-wide text-zinc-400 mb-1">
                Display name{' '}
                <span className="text-zinc-600 normal-case">(optional)</span>
              </label>
              <input
                placeholder="Reelsmith TikTok"
                value={tikDisplayName}
                onChange={(e) => setTikDisplayName(e.target.value)}
                className="w-full bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm"
              />
            </div>
            <div className="flex items-center gap-3 mt-1">
              <button
                type="button"
                disabled={
                  !tikHandle || !tikCookiesJson || tikConnectMutation.isPending
                }
                onClick={() => tikConnectMutation.mutate()}
                className="text-xs px-3 py-1.5 rounded-md border border-white/20 hover:border-white/40 disabled:opacity-50"
              >
                {tikConnectMutation.isPending ? 'Connecting…' : 'Connect TikTok'}
              </button>
              {error ? (
                <p className="text-xs text-red-400">{error}</p>
              ) : (
                <p className="text-xs text-zinc-500">
                  Cookies are encrypted at rest before storage.
                </p>
              )}
            </div>
          </div>
        ) : (
          /* ── Generic access-token form (YouTube / Instagram / etc.) ───── */
          <div className="space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <input
                placeholder="@handle"
                value={handle}
                onChange={(e) => setHandle(e.target.value)}
                className="bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm"
              />
              <input
                placeholder="Access token"
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm"
              />
            </div>
            <div className="flex items-center gap-3">
              <button
                type="button"
                disabled={!handle || !token || createMutation.isPending}
                onClick={() => createMutation.mutate()}
                className="text-xs px-3 py-1.5 rounded-md border border-white/20 hover:border-white/40 disabled:opacity-50"
              >
                {createMutation.isPending ? 'Connecting…' : 'Connect'}
              </button>
              {error ? (
                <p className="text-xs text-red-400">{error}</p>
              ) : (
                <p className="text-xs text-zinc-500">
                  Tokens are encrypted at rest before storage.
                </p>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-white/10 p-5">
        <h2 className="text-sm uppercase tracking-wide text-zinc-400 mb-4">
          Connected
        </h2>
        {accountsQuery.isLoading ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : (accountsQuery.data ?? []).length === 0 ? (
          <p className="text-sm text-zinc-500">No accounts connected yet.</p>
        ) : (
          <ul className="divide-y divide-white/5">
            {(accountsQuery.data ?? []).map((a) => (
              <li
                key={a.id}
                className="flex items-center justify-between py-2.5"
              >
                <div>
                  <p className="text-sm flex items-center flex-wrap gap-x-1">
                    <span className="capitalize">{a.platform}</span>
                    <span className="text-zinc-400">— {a.account_handle}</span>
                    {a.platform === 'tiktok' && (
                      <ExpiryBadge expiresAt={a.expires_at} />
                    )}
                  </p>
                  <p className="text-[11px] text-zinc-500">
                    Connected {new Date(a.created_at).toLocaleString()}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => deleteMutation.mutate(a.id)}
                  className="text-xs text-red-400 hover:text-red-300"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
