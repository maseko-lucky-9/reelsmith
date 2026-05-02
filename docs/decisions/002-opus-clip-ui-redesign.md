# ADR 002 — Opus Clip UI Redesign

**Date**: 2026-05-02  
**Status**: Accepted  
**Author**: Thulani Maseko

---

## Problem

Reelsmith's React frontend was functionally complete (job creation, SSE progress, clip grid, transcription) but visually plain — a default Tailwind/shadcn layout with no clear visual hierarchy, no brand identity, and UX patterns that made the clip-creation workflow unclear. Users had no guided path from pasting a URL to reviewing clips.

---

## Decision

Replicate the Opus Clip UI design system as verified via Playwright-captured screenshots of `clip.opus.pro`. All design decisions are grounded in observed behavior, not assumptions.

Key patterns adopted:

- **True-black palette** — `#000` / `oklch(0.04 0 0)` background, `#4ade80` green accent for scores
- **Left collapsible sidebar** — 48px icon-only / 220px expanded, CSS `width` transition, `localStorage` persistence
- **Dismissible announcement banner** — `sessionStorage`-backed, one dismiss per session
- **Two-step job creation** — `/` dashboard URL input → `/workflow?url=` settings page → POST job → `/jobs/:id`
- **Dual clip views** — list (default) and grid (`?layout=grid`), toggled per project
- **Green letter-grade scores** — `scoreToGrade()` maps 0–100 numeric scores to A/A-/B+/B/B-/C+ labels
- **Clip editor chrome** — full-page layout with transcript panel, portrait video, icon tool sidebar, bottom timeline
- **Brand template page** — settings panel with AI toggles, live CSS preview

---

## Options Considered

| Option | Trade-off |
|--------|-----------|
| Custom design from scratch | Maximum freedom; high effort; no proven UX reference |
| Replicate Opus Clip (chosen) | Proven UX patterns verified via Playwright; faster to implement; risk of divergence as Opus Clip evolves |
| Off-the-shelf dashboard template | Low effort; doesn't fit video/clip domain; generic look |

---

## Consequences

**Positive**:
- Clear visual hierarchy — users have a guided path from URL paste to clip review
- Green score numbers provide immediate quality signal without requiring users to interpret raw numbers
- Sidebar layout scales: adding future nav items requires only a new entry in `Sidebar.tsx`
- Brand template page maps directly to existing backend `/api/brand-templates` CRUD

**Negative / Ongoing**:
- Full inline video editing (multi-track timeline, remotion/ffmpeg.wasm) is out of scope — the clip editor delivers chrome and read-only transcript only
- Opus Clip-specific features (Publish on Social, Export XML, AI hook, Add B-Roll, Enhance speech) are stubbed as "Coming soon" — each requires separate backend work
- YouTube thumbnail in the workflow page uses `img.youtube.com/vi/` directly — works for YouTube URLs only; other video sources show no thumbnail

---

## New Files Added

| File | Purpose |
|------|---------|
| `web/src/components/layout/Sidebar.tsx` | Collapsible left sidebar |
| `web/src/components/layout/TopBar.tsx` | Right-aligned utility bar |
| `web/src/components/layout/AnnouncementBanner.tsx` | Dismissible top strip |
| `web/src/routes/workflow.tsx` | Two-step job creation wizard |
| `web/src/routes/clips.$clipId.edit.tsx` | Clip editor chrome |
| `web/src/routes/settings.brand.tsx` | Brand template page |
| `web/src/components/dashboard/ProjectCard.tsx` | Landscape job card |
| `web/src/components/dashboard/ClipListRow.tsx` | List-view clip row |
| `web/src/components/dashboard/BrandTemplateCard.tsx` | Brand template card |
| `web/src/lib/scoreToGrade.ts` | Score → letter grade helper |
| `web/tests/e2e/dashboard-redesign.spec.ts` | Playwright E2E specs (9 suites) |

---

## Backend Changes

- `GET /api/jobs/preview?url=` — yt-dlp metadata fetch (no download), returns `{title, duration, resolution}`
- `PATCH /api/clips/{id}/like` / `PATCH /api/clips/{id}/dislike` — toggle with mutual exclusion
- `ClipRecord.liked` / `ClipRecord.disliked` columns — added via Alembic migration `5287abbdb094`
