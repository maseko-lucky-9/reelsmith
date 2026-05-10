/**
 * /settings/social — manage social accounts (W1.14).
 *
 * List, create (with platform + handle + access token), and delete
 * connected social identities. Tokens are Fernet-encrypted server-side
 * by the W1.3 token vault before storage.
 */
import { createRoute } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { rootRoute } from './root'
import { api } from '@/api/client'
import type { SocialAccount } from '@/api/client'

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

function SocialAccountsPage() {
  const queryClient = useQueryClient()
  const accountsQuery = useQuery({
    queryKey: ['social-accounts'],
    queryFn: () => api.listSocialAccounts(),
  })

  const [platform, setPlatform] = useState<SocialAccount['platform']>('youtube')
  const [handle, setHandle] = useState('')
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)

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

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteSocialAccount(id),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['social-accounts'] }),
  })

  return (
    <div className="max-w-3xl mx-auto px-6 py-10 text-zinc-200">
      <h1 className="text-2xl font-semibold mb-6">Social accounts</h1>

      <section className="rounded-lg border border-white/10 p-5 mb-8">
        <h2 className="text-sm uppercase tracking-wide text-zinc-400 mb-4">
          Connect a new account
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <select
            value={platform}
            onChange={(e) =>
              setPlatform(e.target.value as SocialAccount['platform'])
            }
            className="bg-zinc-900 border border-white/10 rounded px-3 py-2 text-sm"
          >
            {PLATFORMS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
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
        <div className="flex items-center gap-3 mt-3">
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
                  <p className="text-sm">
                    <span className="capitalize">{a.platform}</span>{' '}
                    <span className="text-zinc-400">— {a.account_handle}</span>
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
