import "@testing-library/jest-dom/vitest";

// jsdom has no media pipeline: play() and pause() only log "Not implemented".
// The editor preview (spec 017) syncs its <video> with the playback store on
// mount, so every page test would print that; tests that assert on them spy.
Object.defineProperty(HTMLMediaElement.prototype, "pause", {
  configurable: true,
  writable: true,
  value: function pause() {},
});
Object.defineProperty(HTMLMediaElement.prototype, "play", {
  configurable: true,
  writable: true,
  value: function play() {
    return Promise.resolve();
  },
});
