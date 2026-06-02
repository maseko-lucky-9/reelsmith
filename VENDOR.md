# Vendored third-party code

## makiisthenes/TiktokAutoUploader

- **Source:** https://github.com/makiisthenes/TiktokAutoUploader
- **Commit:** 73475dbb67be5d8e5e7181af665fbf7f0db7fff4
- **Vendored:** 2026-06-02
- **Location:** `app/vendor/tiktok_uploader/`
- **Why vendored:** No PyPI release; the upload path requires a co-located
  `tiktok-signature/` Node.js bundle (`playwright-chromium`) that must sit
  relative to the Python module. Vendoring keeps both together and pins the
  exact revision.
- **What was excluded:** `Browser.py` (Selenium/undetected-chromedriver),
  `Video.py` (moviepy/yt-dlp). `__init__.py` was rewritten to export only
  what the adapter uses.
- **Upstream changes:** pin this file before upgrading. Breaking changes are
  common — TikTok frequently rotates API endpoints and anti-bot measures.
- **Setup:** `scripts/tiktok-setup.sh` installs the Node bundle. See
  `requirements-tiktok.txt` for Python deps.
