// Platform detection for the URL-input chip on /jobs/new.
// Mirror of `app/services/platforms/__init__.py` — keep regex hosts in sync.
// `upload://` is preserved as a passthrough for internal uploaded videos.

export type PlatformId = 'youtube' | 'facebook' | 'tiktok' | 'instagram' | 'upload' | 'unsupported'

const HOSTS: Record<Exclude<PlatformId, 'upload' | 'unsupported'>, string[]> = {
  youtube: ['youtube.com', 'youtu.be'],
  facebook: ['facebook.com', 'fb.watch'],
  tiktok: ['tiktok.com'],
  instagram: ['instagram.com', 'instagr.am'],
}

const LABELS: Record<PlatformId, string> = {
  youtube: 'YouTube',
  facebook: 'Facebook',
  tiktok: 'TikTok',
  instagram: 'Instagram',
  upload: 'Upload',
  unsupported: 'Unsupported',
}

function hostMatches(url: string, hosts: string[]): boolean {
  const pattern = new RegExp(
    `^https?://(?:[^/]+\\.)?(?:${hosts.map(escapeRegex).join('|')})(?:/|$)`
  )
  return pattern.test(url)
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function detectPlatform(url: string): PlatformId {
  if (!url) return 'unsupported'
  if (url.startsWith('upload://')) return 'upload'
  for (const [id, hosts] of Object.entries(HOSTS) as [
    Exclude<PlatformId, 'upload' | 'unsupported'>,
    string[],
  ][]) {
    if (hostMatches(url, hosts)) return id
  }
  return 'unsupported'
}

export function platformLabel(id: PlatformId): string {
  return LABELS[id]
}

/**
 * True when the URL is short-form 9:16 source (YouTube Shorts, TikTok, Instagram Reel).
 * Used to opt these platforms into Render 9:16 by default in the Advanced tab.
 */
export function isShortFormUrl(url: string): boolean {
  if (!url) return false
  const platform = detectPlatform(url)
  if (platform === 'tiktok' || platform === 'instagram') return true
  if (platform === 'youtube') {
    return /^https?:\/\/(?:[^/]+\.)?(?:youtube\.com|youtu\.be)\/shorts\//i.test(url)
  }
  return false
}
