/** /calendar — scheduled posts (W3.11). */
import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './root'

export const calendarRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/calendar',
  component: CalendarPage,
})

function CalendarPage() {
  // Minimal day-list placeholder. Real calendar grid arrives once the
  // Postgres scheduler worker is running and we have data to render.
  const today = new Date()
  const days: Date[] = []
  for (let i = 0; i < 7; i++) {
    const d = new Date(today)
    d.setDate(d.getDate() + i)
    days.push(d)
  }
  return (
    <div className="max-w-4xl mx-auto px-6 py-10 text-zinc-200">
      <h1 className="text-2xl font-semibold mb-6">Calendar</h1>
      <p className="text-sm text-zinc-400 mb-4">
        Upcoming scheduled publishes. Run the Postgres scheduler worker
        (W3.2) to see queued posts here.
      </p>
      <ul className="space-y-2">
        {days.map((d) => (
          <li
            key={d.toISOString()}
            className="rounded-md border border-white/10 px-4 py-3 flex items-center justify-between"
          >
            <span className="text-sm">
              {d.toLocaleDateString(undefined, {
                weekday: 'short',
                month: 'short',
                day: 'numeric',
              })}
            </span>
            <span className="text-xs text-zinc-500">no posts scheduled</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
