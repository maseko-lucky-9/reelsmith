import { createRouter, createBrowserHistory } from '@tanstack/react-router'
import { routeTree } from './routeTree'

const history = createBrowserHistory()

export const router = createRouter({ routeTree, history })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
