import { rootRoute } from './routes/root'
import { indexRoute } from './routes/index'
import { jobsNewRoute } from './routes/jobs.new'
import { jobDetailRoute } from './routes/jobs.$jobId'
import { clipDetailRoute } from './routes/clips.$clipId'

export const routeTree = rootRoute.addChildren([
  indexRoute,
  jobsNewRoute,
  jobDetailRoute,
  clipDetailRoute,
])
