/**
 * Credits for the B-roll in a rendered reel (FR-010, T039).
 *
 * Pexels' licence does not require attribution, but its API guidelines ask to
 * credit the videographer where possible and to show a prominent link to
 * Pexels whenever the API is used. One line per distinct asset; the
 * server-side `path` and the local file name are never shown, and only
 * absolute http(s) source URLs become links.
 */
import { useId, type ReactNode } from 'react'
import { ExternalLink } from 'lucide-react'
import type { BrollAsset } from '@/api/client'
import { cn } from '@/lib/utils'

const PEXELS_URL = 'https://www.pexels.com'

const LINK_CLASS =
  'text-foreground underline underline-offset-2 decoration-foreground/40 hover:decoration-foreground'

/** `raw` when it is an absolute http(s) URL, else null (javascript:, data:, relative, ...). */
function safeHttpUrl(raw: string | null | undefined): string | null {
  if (!raw) return null
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    return null
  }
  return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
}

interface Credit {
  key: string
  provider: string
  author: string
  url: string | null
}

/** One credit per distinct (provider, asset_id), in insert order, like the manifests. */
function distinctCredits(assets: BrollAsset[]): Credit[] {
  const seen = new Set<string>()
  const credits: Credit[] = []
  for (const asset of assets) {
    const provider = String(asset.provider ?? '')
    const key = JSON.stringify([provider, String(asset.asset_id ?? '')])
    if (seen.has(key)) continue
    seen.add(key)
    credits.push({
      key,
      provider,
      author: String(asset.author ?? '').trim(),
      url: safeHttpUrl(asset.source_url),
    })
  }
  return credits
}

function isPexels(provider: string): boolean {
  return provider.toLowerCase() === 'pexels'
}

function providerLabel(provider: string): string {
  if (isPexels(provider)) return 'Pexels'
  return provider || 'an unknown source'
}

function ExternalAnchor({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={LINK_CLASS}>
      {children}
    </a>
  )
}

function CreditLine({ credit }: { credit: Credit }) {
  const { provider, author, url } = credit
  if (provider === 'local') {
    // The local library has no credit metadata yet; its file name stays on the server.
    return <>{author ? `Video by ${author} (local library)` : 'Local library clip'}</>
  }
  const source = providerLabel(provider)
  if (!author) {
    return url ? <ExternalAnchor href={url}>Video on {source}</ExternalAnchor> : <>Video on {source}</>
  }
  return (
    <>
      Video by {url ? <ExternalAnchor href={url}>{author}</ExternalAnchor> : author} on {source}
    </>
  )
}

interface BrollCreditsProps {
  assets: BrollAsset[] | null | undefined
  className?: string
}

/** Nothing is rendered for a clip without B-roll. */
export function BrollCredits({ assets, className }: BrollCreditsProps) {
  const headingId = useId()
  const credits = distinctCredits(assets ?? [])
  if (credits.length === 0) return null
  const fromPexels = credits.some((c) => isPexels(c.provider))

  return (
    <section aria-labelledby={headingId} className={cn('space-y-1.5 text-xs', className)}>
      <h3
        id={headingId}
        className="text-[11px] font-semibold uppercase tracking-wide text-foreground/70"
      >
        B-roll credits
      </h3>
      <ul className="space-y-0.5 text-foreground/80">
        {credits.map((credit) => (
          <li key={credit.key}>
            <CreditLine credit={credit} />
          </li>
        ))}
      </ul>
      {fromPexels && (
        <a
          href={PEXELS_URL}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(LINK_CLASS, 'inline-flex items-center gap-1 font-medium')}
        >
          Videos provided by Pexels
          <ExternalLink className="w-3 h-3" aria-hidden="true" />
        </a>
      )}
    </section>
  )
}
