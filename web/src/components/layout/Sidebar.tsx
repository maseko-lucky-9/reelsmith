import { useEffect, useState } from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import {
  Home,
  Sparkles,
  LayoutTemplate,
  FolderOpen,
  Share2,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useMediaQuery } from '@/hooks/useMediaQuery'

const STORAGE_KEY = 'sidebar-expanded'
// Below Tailwind's md breakpoint the sidebar is an icon rail that opens as an overlay.
const NARROW_QUERY = '(max-width: 767px)'

interface NavItem {
  label: string
  icon: React.ComponentType<{ className?: string }>
  to: string
  placeholder?: boolean
}

const CREATE_ITEMS: NavItem[] = [
  { label: 'Home', icon: Home, to: '/' },
  { label: 'Generate', icon: Sparkles, to: '/generate/new' },
  { label: 'Brand template', icon: LayoutTemplate, to: '/settings/brand' },
  { label: 'Asset library', icon: FolderOpen, to: '/', placeholder: true },
]

const POST_ITEMS: NavItem[] = [
  // hidden: no backend, see specs roadmap (Analytics -> /analytics)
  { label: 'Social accounts', icon: Share2, to: '/settings/social' },
]

export function Sidebar() {
  const [expanded, setExpanded] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) !== 'false'
    } catch {
      return true
    }
  })

  const narrow = useMediaQuery(NARROW_QUERY)
  // `expanded` is the saved desktop preference; the drawer is narrow-screen only
  // and never saved, so using a phone doesn't change the desktop layout.
  const [drawerOpen, setDrawerOpen] = useState(false)
  const showLabels = narrow ? drawerOpen : expanded
  const overlay = narrow && drawerOpen

  const routerState = useRouterState()
  const pathname = routerState.location.pathname
  const [lastPath, setLastPath] = useState(pathname)
  if (pathname !== lastPath) {
    setLastPath(pathname)
    setDrawerOpen(false)
  }

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(expanded))
    } catch {}
  }, [expanded])

  function isActive(to: string) {
    if (to === '/' || to === '#') return pathname === to
    return pathname.startsWith(to)
  }

  return (
    <>
      {overlay && (
        <div
          data-testid="sidebar-backdrop"
          className="fixed inset-0 z-30 bg-black/50"
          onClick={() => setDrawerOpen(false)}
        />
      )}
      {/* Keeps the page where the icon rail was while the drawer floats over it. */}
      {overlay && <div className="w-12 flex-shrink-0" aria-hidden />}
      <aside
        className={cn(
          'flex flex-col flex-shrink-0 h-screen overflow-hidden transition-[width] duration-200 ease-in-out border-r border-white/8',
          overlay ? 'fixed left-0 top-0 z-40 shadow-2xl' : 'sticky top-0',
        )}
        style={{
          width: showLabels ? '220px' : '48px',
          background: 'var(--sidebar-bg)',
        }}
      >
        {/* Toggle button */}
        <div className="flex items-center justify-end px-2 pt-3 pb-2">
          <button
            onClick={() => (narrow ? setDrawerOpen(!drawerOpen) : setExpanded(!expanded))}
            className="p-1.5 rounded-md text-zinc-400 hover:text-white hover:bg-white/10 transition-colors"
            title={showLabels ? 'Collapse sidebar' : 'Expand sidebar'}
          >
            {showLabels ? <ChevronLeft className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </button>
        </div>

        {/* Create section */}
        <NavSection label="Create" expanded={showLabels}>
          {CREATE_ITEMS.map((item) => (
            <NavItemRow
              key={item.label}
              item={item}
              expanded={showLabels}
              active={isActive(item.to)}
            />
          ))}
        </NavSection>

        {/* Post section */}
        <NavSection label="Post" expanded={showLabels}>
          {POST_ITEMS.map((item) => (
            <NavItemRow
              key={item.label}
              item={item}
              expanded={showLabels}
              active={false}
            />
          ))}
        </NavSection>
      </aside>
    </>
  )
}

function NavSection({
  label,
  expanded,
  children,
}: {
  label: string
  expanded: boolean
  children: React.ReactNode
}) {
  return (
    <div className="mt-4">
      {expanded && (
        <p className="px-3 mb-1 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
          {label}
        </p>
      )}
      <div className="flex flex-col gap-0.5 px-1.5">{children}</div>
    </div>
  )
}

function NavItemRow({
  item,
  expanded,
  active,
}: {
  item: NavItem
  expanded: boolean
  active: boolean
}) {
  const Icon = item.icon

  const inner = (
    <span
      className={cn(
        'flex items-center gap-3 rounded-md px-2 py-2 text-sm transition-colors w-full',
        active
          ? 'bg-white/15 text-white font-medium'
          : 'text-zinc-400 hover:bg-white/8 hover:text-white',
        item.placeholder && 'cursor-not-allowed opacity-50 pointer-events-none',
      )}
      title={!expanded ? item.label : item.placeholder ? 'Coming soon' : undefined}
    >
      <Icon className="w-4 h-4 flex-shrink-0" />
      {expanded && (
        <span className="truncate">{item.label}</span>
      )}
    </span>
  )

  if (item.placeholder || item.to === '#') {
    return <span className="w-full">{inner}</span>
  }

  return (
    <Link to={item.to} className="w-full">
      {inner}
    </Link>
  )
}
