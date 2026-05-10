import { rootRoute } from './routes/root'
import { indexRoute } from './routes/index'
import { jobsNewRoute } from './routes/jobs.new'
import { jobDetailRoute } from './routes/jobs.$jobId'
import { clipDetailRoute } from './routes/clips.$clipId'
import { workflowRoute } from './routes/workflow'
import { clipEditRoute } from './routes/clips.$clipId.edit'
import { brandTemplateRoute } from './routes/settings.brand'
import { socialAccountsRoute } from './routes/settings.social'
import { clipPublishRoute } from './routes/clips.$clipId.publish'
import { captionsSettingsRoute } from './routes/settings.captions'

export const routeTree = rootRoute.addChildren([
  indexRoute,
  jobsNewRoute,
  jobDetailRoute,
  clipDetailRoute,
  workflowRoute,
  clipEditRoute,
  brandTemplateRoute,
  socialAccountsRoute,
  clipPublishRoute,
  captionsSettingsRoute,
])
