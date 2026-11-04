# ADR-014: Editor Timeline Schema v2 (Source Segments, Caption Words, Voice-over Audio)

**Status:** Accepted
**Date:** 2026-11-04
**Author:** Thulani Maseko
**Implements:** [spec 016](../../specs/016-editor-render/spec.md)

ADR-014 was the next free number on this branch (the last file is ADR-013). Spec numbers and ADR numbers are separate sequences.

## Context

The editor saved a v1 timeline whose `video` items were pieces of the already-rendered reel on the reel's clock. That cannot express "extend a clip past its trimmed window", cannot carry per-word caption timings or a caption look, and has no place for a voice-over. The server must now render the timeline (spec 016) from the job's source video, in the same single ffmpeg pass as ADR-004, and the timeline is client-supplied JSON, so its file references and numbers need hard limits.

## Decision

A timeline with top-level `"schema_version": 2` is v2. No `schema_version` (or null) is v1; any other value is rejected. The version is stored inside `clip_edits.timeline` (a JSON column), so there is no migration.

| Part | v1 | v2 |
|---|---|---|
| `video` item | `{start, end, src, trim_start}` on the reel's clock | `{start, end, src?}` one **segment** of the source video, in **source seconds**. Segments play in the order given, back to back; they must be in source order, must not overlap, last at least 0.05 s and lie inside the source's range. The reel's clock is their concatenation. |
| `caption` item | `{start, end, text?, style?, words?}` | `{start, end, text, words[]}` on the reel's clock. Same word count as `words`: each word keeps its timing (clamped to the caption) and takes the edited spelling. Changed count: the text's words are spread evenly over the caption (a `ponytail:` comment names the ceiling). |
| `text-overlay` item | `{start, end, text, x, y, font_size?, color?}` | the same, on the reel's clock, ending no later than the reel |
| `audio` item | not allowed | `{start, end, src, gain}` voice-over on the reel's clock. `src` is a **bare file name** resolved by the server inside the clip's own folder. `gain` in [0, 4]. |
| `caption_style` (top level) | ignored | a key of `animated_caption_service.PRESETS` or null |
| `mute_source` (top level) | ignored | boolean; drops the source audio |

Limits: 16 tracks; at most 100 video, 2000 caption, 100 text-overlay and 16 audio items; 500 characters per text; 100 words per caption; a reel of at most 20 minutes.

Rules that cut across the table:

- **File names, never paths.** `audio.src` has no separator, `.`/`..` or NUL byte, an audio extension (`.wav .mp3 .m4a .aac .ogg .opus .flac`), and must resolve to a regular file directly inside the clip's folder (a symlink leaving the folder is refused). The resolved path is kept server-side and left out of `RenderPlan.to_dict`.
- **Fixed duck.** While a voice-over plays, the source audio is ducked to 0.25 (`VOICEOVER_DUCK_VOLUME`, about -12 dB). It is a constant, not a timeline field or a setting.
- **v1 still loads.** A v1 timeline is validated and stored as sent (the editor's zero-length placeholder video item stays legal on save, not on render), planned against the clip file, and re-based onto the source when re-rendered. Opening a v1 clip in the editor upgrades it to v2 on save; the voice-over route refuses a saved v1 edit (409).
- **One validator.** `PUT /clips/{id}/edit` (`validate_timeline`) and the render (`build_render_plan`) run the same `_parse`; the only difference is the v1 placeholder. The server supplies the source range and the clip folder; the client supplies neither.

## Alternatives considered

- **Keep v1 and add optional fields.** Rejected: v1 video items mean "pieces of the rendered reel", v2 means "windows of the source". The same field names with different clocks would misread old edits.
- **Audio `src` as a path or URL.** Rejected: a client-chosen path reaches ffmpeg as an input (read of any file). A file name in the clip's own folder keeps the choice with the server.
- **Duck level as an item field.** Deferred: one constant covers the one use (a spoken hook over the source); a field can be added in v2 without a version bump if it is optional.
- **A second `ass=` pass for caption styles.** Rejected: ADR-004's one-pass render and the existing PNG caption track already draw per-word captions; a style is a preset's colours and size.

## Consequences

- Old edits keep working; new edits render from the source video, so a clip can be extended beyond its first window.
- The timeline's word timings are the reel's captions: after a re-render the clip's words sidecar is rewritten from them.
- A caption whose word count changed loses its speech rhythm (accepted, see the `ponytail:` comment).
- The wav of a deleted `audio` item stays in the clip folder until the job is deleted.
- Any later schema change adds `schema_version: 3` to `_KINDS_BY_VERSION`; an unknown version is rejected, never guessed.
