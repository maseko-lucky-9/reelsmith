/**
 * The click that ends a drag on the timeline (PR #97 review, should-fix 2).
 *
 * The browser follows a mouseup with a click on the element under both the
 * press and the release: after an edge resize or a selection box inside one
 * row that is the row (or the tracks), whose click handlers clear the
 * selection and move the playhead. The drag calls `suppressNextClick()` on
 * its mouseup; the timeline's capture-phase click handler swallows that one
 * click with `takeSuppressedClick()`. The click is dispatched in the same
 * task as the mouseup, so the flag clears on the next task: a release with
 * no click on the timeline (outside it, or outside the window) never eats a
 * later one.
 */
let pending = false;
let timer: ReturnType<typeof setTimeout> | null = null;

/** The next click (dispatched with this mouseup) ends a drag: the timeline ignores it. */
export function suppressNextClick(): void {
  pending = true;
  if (timer !== null) clearTimeout(timer);
  timer = setTimeout(() => {
    pending = false;
    timer = null;
  }, 0);
}

/** Whether this click ends a drag (and consumes the flag). */
export function takeSuppressedClick(): boolean {
  if (!pending) return false;
  pending = false;
  return true;
}
