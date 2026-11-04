/**
 * The preview's reel layout (spec 017 FR-014, research R11): a mirror of
 * `app/services/clip_service.reel_geometry`, so captions sit where the render
 * puts them. Python's integer arithmetic is kept exactly: `int()` truncates
 * (`Math.trunc`) and `//` floors (`Math.floor`, which differs for the
 * negative inset top of a source taller than 9:16).
 *
 * `web/src/editor/reel-geometry-cases.json` holds cases checked against this
 * file (`preview.test.ts`) and against the Python function
 * (`tests/unit/test_editor_preview_assets.py`).
 */

/** The standard reel width; wider sources get a 1080 px canvas (MAX_CANVAS_WIDTH). */
export const MAX_CANVAS_WIDTH = 1080;
/** A blur band shorter than canvas_h // 10 cannot hold a caption (MIN_BAND_DIVISOR). */
export const MIN_BAND_DIVISOR = 10;
/** Without such a band the caption anchors at 85% of the canvas height (FILLED_CANVAS_ANCHOR, PR #96). */
export const FILLED_CANVAS_ANCHOR = 0.85;
export const REEL_ASPECT_RATIO = 9 / 16;

export interface ReelGeometry {
  sourceSize: [number, number];
  canvasSize: [number, number];
  /** The source scaled to the canvas width (ffmpeg scale=W:-2: rounded half-up to even). */
  insetHeight: number;
  /** The inset's top edge, vertically centred; negative when the source is taller than the canvas. */
  insetTop: number;
  /** The caption's vertical centre on the canvas. */
  bandAnchorY: number;
}

export function reelGeometry(
  sourceW: number,
  sourceH: number,
  targetAspectRatio: number = REEL_ASPECT_RATIO,
): ReelGeometry {
  const canvasW = Math.min(sourceW, MAX_CANVAS_WIDTH);
  const canvasH = Math.trunc(canvasW / targetAspectRatio);
  const insetHeight =
    2 * Math.floor((sourceH * canvasW + sourceW) / (2 * sourceW));
  const insetTop = Math.floor((canvasH - insetHeight) / 2);
  const innerBottom = insetTop + insetHeight;
  const band = canvasH - innerBottom;
  const bandAnchorY =
    band < Math.floor(canvasH / MIN_BAND_DIVISOR)
      ? Math.trunc(canvasH * FILLED_CANVAS_ANCHOR)
      : innerBottom + Math.floor(band / 2);
  return {
    sourceSize: [sourceW, sourceH],
    canvasSize: [canvasW, canvasH],
    insetHeight,
    insetTop,
    bandAnchorY,
  };
}

/** Canvas pixels to preview pixels: preview width / canvas width. */
export function fontScale(
  previewWidth: number,
  geometry: ReelGeometry,
): number {
  return previewWidth / geometry.canvasSize[0];
}
