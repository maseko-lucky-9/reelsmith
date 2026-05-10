/** /settings/api — REST API tokens (W3.12). */
import { createRoute } from '@tanstack/react-router'
import { rootRoute } from './root'

export const apiSettingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/api',
  component: ApiSettingsPage,
})

function ApiSettingsPage() {
  return (
    <div className="max-w-3xl mx-auto px-6 py-10 text-zinc-200">
      <h1 className="text-2xl font-semibold mb-6">API tokens</h1>
      <p className="text-sm text-zinc-400 mb-4">
        Issue REST API tokens for scripting / automation. Tokens are
        stored as bcrypt hashes; the plaintext is shown only once at
        creation time.
      </p>
      <p className="text-xs text-zinc-500">
        Programmatic creation lands in a follow-up — for now use the
        backend CLI:
      </p>
      <pre className="mt-2 text-xs bg-zinc-900 border border-white/10 rounded p-3 overflow-x-auto">
{`python -c "
import asyncio
from app.db.engine import get_engine
from app.db.session import get_session_factory
from app.services.api_token_service import create_token

async def main():
    factory = get_session_factory()
    async with factory() as s:
        plain, _ = await create_token(s, name='cli')
        print(plain)
asyncio.run(main())
"`}
      </pre>
    </div>
  )
}
