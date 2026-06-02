#!/usr/bin/env bash
# Install the tiktok-signature Node bundle required by the cookie adapter.
# Run once after cloning: scripts/tiktok-setup.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIG_DIR="$REPO_ROOT/app/vendor/tiktok_uploader/tiktok-signature"

command -v node >/dev/null 2>&1 || { echo "ERROR: Node 18+ is required. Install via nvm or brew." >&2; exit 1; }
node_major=$(node --version | sed 's/v//' | cut -d. -f1)
if [ "$node_major" -lt 18 ]; then
  echo "ERROR: Node 18+ required (found $(node --version))." >&2; exit 1
fi

echo "Installing tiktok-signature npm deps in $SIG_DIR..."
cd "$SIG_DIR"
npm install
npx playwright install chromium

echo "TikTok signature bundle ready."
echo "Now set YTVIDEO_SOCIAL_PROVIDER_TIKTOK=cookie in .env to activate the adapter."
