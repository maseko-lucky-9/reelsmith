# ADR-016: One Folder per Source, Grouped Export Behind a Flag

**Status:** Accepted (implemented on `feat/018-source-folders`)
**Date:** 2026-11-16
**Author:** Thulani Maseko
**Implements:** [spec 018](../../specs/018-source-folders/spec.md) · **Builds on:** [ADR-010](010-map-export-folder.md), [ADR-012](012-tiktok-posting-gatekeeper.md)

## Context

Job folders and the export inbox were flat. The publisher lives in `n8n-self-hosting` and the uploader in `tiktok-sidecar`; both read `<inbox>/<job_id>/` (the sidecar accepts exactly `<job>/<file>`). The "not saved" report was a render-off job plus a UI that hid it (spec 018, Problem).

## Decision

1. Local job folders nest under a per-source folder named in `folder_service.SOURCE_FOLDERS` (plural names). Retention is unchanged: a grouped folder is still strictly below the managed root.
2. The export inbox groups the same way only when `YTVIDEO_EXPORT_GROUP_BY_SOURCE` is on. It stays off until the sidecar and the workflow read the grouped layout, so merging Reelsmith cannot stall posting. Both consumers keep reading the flat layout, so jobs already exported are unaffected.
3. The publisher scans `YouTube Videos/` (and the flat folder) only, so "YouTube only" is structural, on top of the existing `source`/`segment_origin` row filter. The sidecar allow-lists that single group name literally.
4. Existing local folders are moved by a script that is dry-run by default; export history is not moved (claim and archive state on the cluster volume would be at risk of a double post).
5. Opening a folder is a server-side `open`/`explorer`/`xdg-open` on a path taken from the job row, restricted to loopback callers.

## Consequences

Rollout order is sidecar, then workflow, then the flag. A grouped clip sent to an old sidecar is a 400 parked as `needs_you`. Job folders have two levels, so anything that assumed `<download_path>/<job folder>` must go through the stored `destination_folder` (retention and re-render already do).
