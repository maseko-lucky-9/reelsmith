import { useState } from 'react'
import { X } from 'lucide-react'

const DISMISS_KEY = 'announcement-dismissed'

export function AnnouncementBanner() {
  const [visible, setVisible] = useState(() => {
    try {
      return sessionStorage.getItem(DISMISS_KEY) !== 'true'
    } catch {
      return true
    }
  })

  if (!visible) return null

  function dismiss() {
    setVisible(false)
    try {
      sessionStorage.setItem(DISMISS_KEY, 'true')
    } catch {}
  }

  return (
    <div className="flex items-center justify-center gap-3 px-4 py-2 text-xs text-zinc-300 bg-zinc-900 border-b border-white/8 relative">
      <span>
        Reelsmith is open source. Star it on{' '}
        <a
          href="https://github.com"
          className="underline text-white hover:text-zinc-300"
          target="_blank"
          rel="noreferrer"
        >
          GitHub
        </a>{' '}
        →
      </span>
      <button
        onClick={dismiss}
        className="absolute right-3 text-zinc-500 hover:text-white transition-colors"
        aria-label="Dismiss"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}
