import { detectPlatform, platformLabel, type PlatformId } from '@/lib/detectPlatform'

type Props = {
  /** Either an explicit platform id (preferred when available from JobState.source) or a URL to detect from. */
  platform?: string | null
  url?: string | null
  className?: string
}

export function PlatformChip({ platform, url, className = '' }: Props) {
  const id = (platform as PlatformId | undefined) ?? (url ? detectPlatform(url) : 'unsupported')
  const isUnsupported = id === 'unsupported'
  const cls = isUnsupported
    ? 'bg-red-900/40 text-red-300 border border-red-800'
    : 'bg-emerald-900/40 text-emerald-300 border border-emerald-800'
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls} ${className}`}
    >
      {platformLabel(id)}
    </span>
  )
}
