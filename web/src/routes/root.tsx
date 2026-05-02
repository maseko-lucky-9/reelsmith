import { createRootRoute, Link, Outlet } from '@tanstack/react-router'
import { Toaster } from '@/components/ui/sonner'

export const rootRoute = createRootRoute({
  component: RootLayout,
})

function RootLayout() {
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <nav className="border-b border-zinc-800 px-6 py-3 flex items-center gap-6">
        <Link to="/" className="text-lg font-bold tracking-tight hover:text-zinc-300">
          Reelsmith
        </Link>
        <Link
          to="/"
          className="text-sm text-zinc-400 hover:text-zinc-100 [&.active]:text-zinc-100"
        >
          Library
        </Link>
        <Link
          to="/jobs/new"
          className="text-sm text-zinc-400 hover:text-zinc-100 [&.active]:text-zinc-100"
        >
          New Job
        </Link>
      </nav>
      <main className="px-6 py-6">
        <Outlet />
      </main>
      <Toaster />
    </div>
  )
}
