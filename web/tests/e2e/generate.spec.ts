import { test, expect, type Page } from '@playwright/test'

/**
 * E2E for the AI generation mode form (/generate/new).
 *
 * Mirrors the job-submit flow used by the other e2e specs: fill the form,
 * submit, assert navigation to /jobs/:jobId, and assert the shared progress
 * timeline becomes visible.
 *
 * Resilient by design — the backend is mocked via page.route so the test does
 * not depend on a real generation render completing. The job detail page is
 * served a `running` JobState so JobProgressTimeline (rendered as <ol
 * role="list">) is visible, exactly as it is for URL/upload jobs.
 *
 * Uses the same playwright.config.ts (baseURL localhost:5173, pnpm dev web
 * server) as the other specs.
 */

const JOB_ID = 'gen-e2e-job-1'
const BRIEF_ID = 'gen-e2e-brief-1'

/** Minimal JobState in `running` state — enough for the detail page to show the timeline. */
const RUNNING_JOB = {
  job_id: JOB_ID,
  status: 'running',
  current_step: 'rendering',
  url: 'generate://gen-e2e-brief-1',
  source: 'generate',
  download_path: '/tmp/gen',
  caption_format: 'srt',
  target_aspect_ratio: 0.5625,
  destination_folder: null,
  clips_folder: null,
  video_path: null,
  title: 'E2E Generated Reel',
  duration: null,
  chapters: {},
  output_paths: [],
  error: null,
  prompt: null,
  pipeline_options: {
    transcription: true,
    captions: true,
    render: true,
    segment_proposer: true,
    reframe: true,
    broll: true,
    thumbnail: true,
  },
}

/** Install backend mocks so the flow runs without a live FastAPI server. */
async function installMocks(page: Page) {
  await page.route('**/api/generate', async (route) => {
    await route.fulfill({
      // Matches the real router (app/routers/generate.py declares status_code=202).
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: JOB_ID, brief_id: BRIEF_ID, status: 'pending' }),
    })
  })

  await page.route(`**/api/jobs/${JOB_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(RUNNING_JOB),
    })
  })

  // Clips list for the detail page (empty while still rendering).
  await page.route('**/api/clips**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  // SSE events stream — keep open with no events; the timeline reads JobState.
  await page.route(`**/api/jobs/${JOB_ID}/events`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: '',
    })
  })
}

test.describe('Generate mode — /generate/new', () => {
  test('fills the brief, submits, and navigates to the job timeline', async ({ page }) => {
    await installMocks(page)

    await page.goto('/generate/new')

    await expect(page.getByRole('heading', { name: 'Generate' })).toBeVisible()

    // Submit is disabled until title + script are filled.
    const submit = page.getByRole('button', { name: 'Generate' })
    await expect(submit).toBeDisabled()

    await page.getByPlaceholder('A short title for this generation').fill('E2E Generated Reel')
    await page
      .getByPlaceholder('The narration / voiceover script…')
      .fill('A calm walkthrough of a misty forest at dawn.')
    await page
      .getByPlaceholder(/A sunrise over mountains/)
      .fill('A sunrise over mountains\nClose-up of dew on a leaf\nWide shot of a forest path')

    await expect(submit).toBeEnabled()
    await submit.click()

    // Navigation to the job detail page.
    await page.waitForURL(new RegExp(`/jobs/${JOB_ID}`), { timeout: 10_000 })
    expect(page.url()).toContain(`/jobs/${JOB_ID}`)

    // The shared progress timeline (<ol role="list">) is visible for a running job.
    const timeline = page.locator('ol[role="list"]').first()
    await expect(timeline).toBeVisible({ timeout: 10_000 })
  })

  test('is reachable from the sidebar Generate link', async ({ page }) => {
    await installMocks(page)

    await page.goto('/')

    // Sidebar may start collapsed — expand it so labels are visible.
    const toggle = page.locator('aside button').first()
    await toggle.click()

    await page.locator('aside').getByText('Generate', { exact: true }).click()
    await page.waitForURL(/\/generate\/new/, { timeout: 10_000 })
    await expect(page.getByRole('heading', { name: 'Generate' })).toBeVisible()
  })
})
