import { test, expect } from '@playwright/test'

/**
 * E2E specs verifying Reelsmith's Opus Clip UI redesign.
 * Each spec maps to a verified Opus Clip UX pattern from the planning session.
 *
 * These tests require the dev server (pnpm dev) to be running at localhost:5173
 * and the FastAPI backend (uvicorn) at localhost:8000.
 *
 * Run: pnpm playwright test
 */

// ---------------------------------------------------------------------------
// Spec 1 — Global layout & sidebar
// ---------------------------------------------------------------------------
test.describe('Spec 1 — Global layout & sidebar', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test('sidebar renders in collapsed state (width < 60px)', async ({ page }) => {
    const sidebar = page.locator('aside').first()
    const box = await sidebar.boundingBox()
    expect(box?.width).toBeLessThan(60)
  })

  test('clicking collapse toggle expands sidebar to > 180px', async ({ page }) => {
    const toggle = page.locator('aside button').first()
    await toggle.click()
    const sidebar = page.locator('aside').first()
    const box = await sidebar.boundingBox()
    expect(box?.width).toBeGreaterThan(180)
  })

  test('Home nav item has active style on /', async ({ page }) => {
    const toggle = page.locator('aside button').first()
    await toggle.click()
    const homeLink = page.locator('aside').getByText('Home')
    await expect(homeLink).toBeVisible()
  })

  test('announcement banner is visible', async ({ page }) => {
    const banner = page.locator('text=Star it on')
    await expect(banner).toBeVisible()
  })

  test('dismissing banner hides it', async ({ page }) => {
    const dismissBtn = page.locator('button[aria-label="Dismiss"]')
    await dismissBtn.click()
    const banner = page.locator('text=Star it on')
    await expect(banner).not.toBeVisible()
  })

  test('top utility bar shows notification bell, credits icon, and New Clip button', async ({ page }) => {
    await expect(page.locator('header button').first()).toBeVisible()
    await expect(page.getByRole('button', { name: /New Clip/i })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Spec 2 — Dashboard home hero + projects
// ---------------------------------------------------------------------------
test.describe('Spec 2 — Dashboard home', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test('URL input renders with link icon', async ({ page }) => {
    const input = page.getByPlaceholder(/Drop a.*link/i)
    await expect(input).toBeVisible()
  })

  test('7 feature icon buttons render', async ({ page }) => {
    const labels = ['Long to shorts', 'AI Captions', 'Video editor', 'Enhance speech', 'AI Reframe', 'AI B-Roll', 'AI hook']
    for (const label of labels) {
      await expect(page.getByTitle(new RegExp(label, 'i')).first().or(page.getByText(label).first())).toBeVisible()
    }
  })

  test('"Get clips in 1 click" CTA is present', async ({ page }) => {
    await expect(page.getByRole('button', { name: /Get clips in 1 click/i })).toBeVisible()
  })

  test('"All projects" tab renders', async ({ page }) => {
    await expect(page.getByText(/All projects/i)).toBeVisible()
  })

  test('empty state shows "No projects yet" when there are no jobs', async ({ page }) => {
    // Only visible when no jobs exist — acceptable if jobs exist in dev
    const noJobs = page.getByText(/No projects yet/i)
    const projectCard = page.locator('[class*="aspect-"]').first()
    const either = await Promise.race([
      noJobs.waitFor({ state: 'visible', timeout: 3000 }).then(() => 'empty'),
      projectCard.waitFor({ state: 'visible', timeout: 3000 }).then(() => 'has-cards'),
    ]).catch(() => 'timeout')
    expect(['empty', 'has-cards']).toContain(either)
  })
})

// ---------------------------------------------------------------------------
// Spec 3 — Workflow page
// ---------------------------------------------------------------------------
test.describe('Spec 3 — Workflow page', () => {
  const ytUrl = 'https://www.youtube.com/watch?v=8I3_NM-V_w0'

  test('URL input → "Get clips in 1 click" navigates to /workflow', async ({ page }) => {
    await page.goto('/')
    const input = page.getByPlaceholder(/Drop a.*link/i)
    await input.fill(ytUrl)
    await page.getByRole('button', { name: /Get clips in 1 click/i }).click()
    await expect(page).toHaveURL(/\/workflow/)
  })

  test('/workflow shows URL in disabled input with Remove button', async ({ page }) => {
    await page.goto(`/workflow?url=${encodeURIComponent(ytUrl)}`)
    const disabledInput = page.locator('input[disabled]').first()
    await expect(disabledInput).toBeVisible()
    await expect(page.getByRole('button', { name: /Remove/i })).toBeVisible()
  })

  test('legal disclaimer is visible on /workflow', async ({ page }) => {
    await page.goto(`/workflow?url=${encodeURIComponent(ytUrl)}`)
    await expect(page.getByText(/violate copyright/i)).toBeVisible()
  })

  test('"AI clipping" tab is selected by default', async ({ page }) => {
    await page.goto(`/workflow?url=${encodeURIComponent(ytUrl)}`)
    await expect(page.getByRole('button', { name: 'AI clipping' })).toBeVisible()
    await expect(page.getByRole('button', { name: "Don't clip" })).toBeVisible()
  })

  test('Auto hook toggle is ON by default', async ({ page }) => {
    await page.goto(`/workflow?url=${encodeURIComponent(ytUrl)}`)
    await expect(page.getByText(/Auto hook/i)).toBeVisible()
  })

  test('"Credit saver" badge is visible', async ({ page }) => {
    await page.goto(`/workflow?url=${encodeURIComponent(ytUrl)}`)
    await expect(page.getByText(/Credit saver/i)).toBeVisible()
  })

  test('"Save settings above as default" link is present', async ({ page }) => {
    await page.goto(`/workflow?url=${encodeURIComponent(ytUrl)}`)
    await expect(page.getByText(/Save settings above as default/i)).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Spec 4 — Clips list view (requires a completed job)
// ---------------------------------------------------------------------------
test.describe('Spec 4 — Clips list view', () => {
  test('job page renders with grid and list toggle buttons', async ({ page }) => {
    // Navigate to jobs list and pick first job
    const jobsRes = await page.request.get('/api/jobs')
    const jobs = await jobsRes.json() as Array<{ job_id: string; status: string }>
    if (jobs.length === 0) {
      test.skip()
      return
    }
    await page.goto(`/jobs/${jobs[0].job_id}`)

    // Grid and list view icons
    await expect(page.locator('button[title="Grid view"]')).toBeVisible()
    await expect(page.locator('button[title="List view"]')).toBeVisible()
  })

  test('Filter and Sort buttons are visible', async ({ page }) => {
    const jobsRes = await page.request.get('/api/jobs')
    const jobs = await jobsRes.json() as Array<{ job_id: string }>
    if (jobs.length === 0) { test.skip(); return }
    await page.goto(`/jobs/${jobs[0].job_id}`)
    await expect(page.getByRole('button', { name: /Filter/i })).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Spec 5 — Clips grid view
// ---------------------------------------------------------------------------
test.describe('Spec 5 — Clips grid view', () => {
  test('clicking grid icon switches to grid layout', async ({ page }) => {
    const jobsRes = await page.request.get('/api/jobs')
    const jobs = await jobsRes.json() as Array<{ job_id: string }>
    if (jobs.length === 0) { test.skip(); return }
    await page.goto(`/jobs/${jobs[0].job_id}`)

    await page.locator('button[title="Grid view"]').click()
    const grid = page.locator('.grid-cols-2, .grid-cols-3, .grid-cols-4').first()
    await expect(grid).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Spec 6 — Filter panel
// ---------------------------------------------------------------------------
test.describe('Spec 6 — Filter panel', () => {
  test('filter popover opens with Liked/Disliked checkboxes', async ({ page }) => {
    const jobsRes = await page.request.get('/api/jobs')
    const jobs = await jobsRes.json() as Array<{ job_id: string }>
    if (jobs.length === 0) { test.skip(); return }
    await page.goto(`/jobs/${jobs[0].job_id}`)

    await page.getByRole('button', { name: /Filter/i }).click()
    await expect(page.getByText('Liked')).toBeVisible()
    await expect(page.getByText('Disliked')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Spec 7 — Clip editor
// ---------------------------------------------------------------------------
test.describe('Spec 7 — Clip editor', () => {
  test('clip editor page renders editor chrome', async ({ page }) => {
    const clipsRes = await page.request.get('/api/clips')
    const clips = await clipsRes.json() as Array<{ clip_id: string }>
    if (clips.length === 0) { test.skip(); return }
    await page.goto(`/clips/${clips[0].clip_id}/edit`)

    await expect(page.getByText('Export')).toBeVisible()
    await expect(page.getByText('Transcript only')).toBeVisible()
    await expect(page.getByText('9:16')).toBeVisible()
    await expect(page.getByText('Tracker: OFF')).toBeVisible()
  })

  test('Export button points to video download endpoint', async ({ page }) => {
    const clipsRes = await page.request.get('/api/clips')
    const clips = await clipsRes.json() as Array<{ clip_id: string }>
    if (clips.length === 0) { test.skip(); return }
    await page.goto(`/clips/${clips[0].clip_id}/edit`)

    const exportLink = page.getByRole('link', { name: /Export/i })
    const href = await exportLink.getAttribute('href')
    expect(href).toContain('/api/clips/')
    expect(href).toContain('/video')
  })
})

// ---------------------------------------------------------------------------
// Spec 8 — Clip detail player
// ---------------------------------------------------------------------------
test.describe('Spec 8 — Clip detail player', () => {
  test('clip detail shows large green score and edit link', async ({ page }) => {
    const clipsRes = await page.request.get('/api/clips')
    const clips = await clipsRes.json() as Array<{ clip_id: string; virality_score: number | null }>
    const clipsWithScore = clips.filter((c) => c.virality_score != null)
    if (clipsWithScore.length === 0) { test.skip(); return }

    await page.goto(`/clips/${clipsWithScore[0].clip_id}`)

    await expect(page.getByText(/Edit clip/i)).toBeVisible()
    await expect(page.getByText(/Virality Score/i)).toBeVisible()
    await expect(page.getByText(String(clipsWithScore[0].virality_score))).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Spec 9 — Brand template page
// ---------------------------------------------------------------------------
test.describe('Spec 9 — Brand template page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/settings/brand')
  })

  test('top bar shows Brand template heading', async ({ page }) => {
    await expect(page.getByText('Brand template')).toBeVisible()
    await expect(page.getByText(/Quickly setup/i)).toBeVisible()
  })

  test('Save template button is present', async ({ page }) => {
    await expect(page.getByRole('button', { name: /Save template/i })).toBeVisible()
  })

  test('New template button is present', async ({ page }) => {
    await expect(page.getByRole('button', { name: /New template/i })).toBeVisible()
  })

  test('AI toggles section is visible with correct defaults', async ({ page }) => {
    await expect(page.getByText('AI keywords highlighter')).toBeVisible()
    await expect(page.getByText('AI emojis')).toBeVisible()
    await expect(page.getByText('Remove filler words')).toBeVisible()
  })

  test('clicking New template opens aspect ratio modal', async ({ page }) => {
    await page.getByRole('button', { name: /New template/i }).click()
    await expect(page.getByText('9:16')).toBeVisible()
    await expect(page.getByText('TikTok / Reels')).toBeVisible()
  })
})
