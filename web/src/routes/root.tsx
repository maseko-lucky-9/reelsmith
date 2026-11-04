import { createRootRoute, Outlet } from '@tanstack/react-router'
import { Toaster } from '@/components/ui/sonner'
import { Sidebar } from '@/components/layout/Sidebar'
import { TopBar } from '@/components/layout/TopBar'
import { AnnouncementBanner } from '@/components/layout/AnnouncementBanner'

export const rootRoute = createRootRoute({
  component: RootLayout,
})

function RootLayout() {
  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <AnnouncementBanner />
        <TopBar />
        <main className="flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-6">
          <Outlet />
        </main>
      </div>
      <Toaster theme="dark" />
    </div>
  )
}
