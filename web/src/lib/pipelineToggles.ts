/**
 * Pipeline toggle definitions for the Advanced panel and future settings pages.
 *
 * Each toggle maps to a key in PipelineOptions (api/client.ts).
 * `dependsOn` / `parent` encode the dependency graph so the UI can
 * auto-disable children when the parent toggle is off.
 */

export type ToggleId =
  | 'transcription'
  | 'captions'
  | 'render'
  | 'segment_proposer'
  | 'reframe'
  | 'broll'
  | 'thumbnail'

export type ToggleGroup = 'transcribe' | 'render' | 'segment'

export interface ToggleDescriptor {
  readonly id: ToggleId
  readonly group: ToggleGroup
  readonly label: string
  readonly helper: string
  /** This toggle auto-disables when the named toggle is off (same-group dependency). */
  readonly dependsOn?: ToggleId
  /** Visual nesting: render this toggle indented under the named parent. */
  readonly parent?: ToggleId
}

export const TOGGLES: readonly ToggleDescriptor[] = [
  {
    id: 'transcription',
    group: 'transcribe',
    label: 'Transcription',
    helper: 'Generate word-level transcript with Whisper.',
  },
  {
    id: 'captions',
    group: 'transcribe',
    label: 'Captions',
    helper: 'Burn karaoke subtitles. Requires Transcription.',
    dependsOn: 'transcription',
  },
  {
    id: 'render',
    group: 'render',
    label: 'Render 9:16 clip',
    helper: 'Produce final vertical clip. Off ⇒ source file only, no per-chapter cuts.',
  },
  {
    id: 'reframe',
    group: 'render',
    label: 'Reframe (face-track)',
    helper: 'Auto-track faces during reframe. Off ⇒ letterbox.',
    parent: 'render',
  },
  {
    id: 'broll',
    group: 'render',
    label: 'B-roll inserts',
    helper: 'Splice topical B-roll into the rendered clip.',
    parent: 'render',
  },
  {
    id: 'thumbnail',
    group: 'render',
    label: 'Thumbnail',
    helper: 'Auto-generate a thumbnail from the rendered output.',
    parent: 'render',
  },
  {
    id: 'segment_proposer',
    group: 'segment',
    label: 'Auto-detect highlights',
    helper:
      'When the source has no chapters, run heuristic scoring to find clip-worthy segments. Off ⇒ one clip per native chapter, or single full-video clip.',
  },
] as const

export const GROUP_LABELS: Record<ToggleGroup, string> = {
  transcribe: 'Transcribe & caption',
  render: 'Render',
  segment: 'Segmentation',
}

/** Default pipeline options when all toggles are ON. */
export const ALL_ON: Record<ToggleId, boolean> = {
  transcription: true,
  captions: true,
  render: true,
  segment_proposer: true,
  reframe: true,
  broll: true,
  thumbnail: true,
}

/** Default pipeline options when all toggles are OFF (Advanced-tab mount default). */
export const ALL_OFF: Record<ToggleId, boolean> = {
  transcription: false,
  captions: false,
  render: false,
  segment_proposer: false,
  reframe: false,
  broll: false,
  thumbnail: false,
}

/**
 * Advanced-tab default for short-form 9:16 sources (YouTube Shorts, TikTok, Instagram Reels).
 * Render is on so the user gets a 9:16 clip; everything else stays opt-in.
 */
export const SHORT_FORM_DEFAULTS: Record<ToggleId, boolean> = {
  transcription: false,
  captions: false,
  render: true,
  segment_proposer: false,
  reframe: false,
  broll: false,
  thumbnail: false,
}
