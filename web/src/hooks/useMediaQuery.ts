import { useCallback, useSyncExternalStore } from 'react'

const supported = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function'

/** Whether ``query`` currently matches; ``false`` where ``matchMedia`` doesn't exist (jsdom, SSR). */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (notify: () => void) => {
      if (!supported()) return () => {}
      const list = window.matchMedia(query)
      list.addEventListener('change', notify)
      return () => list.removeEventListener('change', notify)
    },
    [query],
  )
  return useSyncExternalStore(
    subscribe,
    () => (supported() ? window.matchMedia(query).matches : false),
    () => false,
  )
}
