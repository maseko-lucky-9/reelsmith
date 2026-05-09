import { Component, type ReactNode } from 'react'

type Props = { children: ReactNode; fallback?: ReactNode }
type State = { error: Error | null }

/**
 * Tiny error boundary so a malformed JobState can't blank the whole job page.
 * Falls back to a minimal status banner if the timeline crashes.
 */
export class TimelineErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error) {
    // eslint-disable-next-line no-console
    console.error('[JobProgressTimeline]', error)
  }

  render() {
    if (this.state.error) {
      return (
        this.props.fallback ?? (
          <div className="rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-3 text-sm text-zinc-400">
            Progress display failed to render. Refresh to retry.
          </div>
        )
      )
    }
    return this.props.children
  }
}
