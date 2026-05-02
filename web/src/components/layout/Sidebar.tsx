import { useEffect, useState } from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import {
  Home,
  LayoutTemplate,
  FolderOpen,
  Calendar,
  BarChart2,
  Share2,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const STORAGE_KEY = 'sidebar-expanded'

interface NavItem {
  label: string
  icon: React.ComponentType<{ className?: string }>
  to: string
  placeholder?: boolean
}

const CREATE_ITEMS: NavItem[] = [
  { label: 'Home', icon: Home, to: '/' },
  { label: 'Brand template', icon: LayoutTemplate, to: '/settings/brand' },
  { label: 'Asset library', icon: FolderOpen, to: '/', placeholder: true },
]

const POST_ITEMS: NavItem[] = [
  { label: 'Calendar', icon: Calendar, to: '#', placeholder: true },
  { label: 'Analytics', icon: BarChart2, to: '#', placeholder: true },
  { label: 'Social accounts', icon: Share2, to: '#', placeholder: true },
]

export function Sidebar() {
  const [expanded, setExpanded] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) !== 'false'
    } catch {
      return true
    }
  })

  const routerState = useRouterState()
  const pathname = routerState.location.pathname

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
    <aside
      className="flex flex-col flex-shrink-0 h-screen sticky top-0 overflow-hidden transition-[width] duration-200 ease-in-out border-r border-white/8"
      style={{
        width: expanded ? '220px' : '48px',
        background: 'var(--sidebar-bg)',
      }}
    >
      {/* Toggle button */}
      <div className="flex items-center justify-end px-2 pt-3 pb-2">
        <button
          onClick={() => setExpanded(!expanded)}
          className="p-1.5 rounded-md text-zinc-400 hover:text-white hover:bg-white/10 transition-colors"
          title={expanded ? 'Collapse sidebar' : 'Expand sidebar'}
        >
          {expanded ? <ChevronLeft className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>
      </div>

      {/* Create section */}
      <NavSection label="Create" expanded={expanded}>
        {CREATE_ITEMS.map((item) => (
          <NavItemRow
            key={item.label}
            item={item}
            expanded={expanded}
            active={isActive(item.to)}
          />
        ))}
      </NavSection>

      {/* Post section */}
      <NavSection label="Post" expanded={expanded}>
        {POST_ITEMS.map((item) => (
          <NavItemRow
            key={item.label}
            item={item}
            expanded={expanded}
            active={false}
          />
        ))}
      </NavSection>
    </aside>
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
