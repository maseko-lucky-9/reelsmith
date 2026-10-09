"""Moving crop (reframe) for the one-pass renderer: the pure parts.

``crop_x_expr`` is checked by evaluating the generated ffmpeg expression with
a tiny independent evaluator (ffmpeg semantics for the subset it emits) and
comparing it with ``numpy.interp`` as the oracle. Real-ffmpeg renders live in
``tests/e2e/test_render_crop_track.py``.
"""

from __future__ import annotations

import random
import re
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from app.services import clip_service, render_service
from app.services.caption_track import CaptionTrack
from app.services.render_service import crop_x_expr, pan_crop

# ── a minimal evaluator for the expression subset crop_x_expr emits ─────────

_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_]+)|(.))")
_FUNCS = {
    "lt": lambda a, b: 1.0 if a < b else 0.0,
    "gte": lambda a, b: 1.0 if a >= b else 0.0,
}


class _Evaluator:
    """Recursive descent over numbers, ``t``, ``+ - * /``, parentheses and
    ``lt``/``gte`` (ffmpeg: ``lt(a,b)`` = a<b, ``gte(a,b)`` = a>=b)."""

    def __init__(self, expr: str):
        self.tokens = [m.groups() for m in _TOKEN.finditer(expr) if m.group(0).strip()]
        self.pos = 0

    def _peek(self):
        return (
            self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None, None)
        )

    def _take(self, op: str) -> None:
        assert self._peek()[2] == op, f"expected {op!r} at token {self.pos}"
        self.pos += 1

    def evaluate(self, t: float) -> float:
        self.t, self.pos = t, 0
        value = self._sum()
        assert self.pos == len(self.tokens), "trailing tokens"
        return value

    def _sum(self) -> float:
        value = self._product()
        while self._peek()[2] in ("+", "-"):
            op = self._peek()[2]
            self.pos += 1
            rhs = self._product()
            value = value + rhs if op == "+" else value - rhs
        return value

    def _product(self) -> float:
        value = self._primary()
        while self._peek()[2] in ("*", "/"):
            op = self._peek()[2]
            self.pos += 1
            rhs = self._primary()
            value = value * rhs if op == "*" else value / rhs
        return value

    def _primary(self) -> float:
        number, name, op = self._peek()
        self.pos += 1
        if number is not None:
            return float(number)
        if name == "t":
            return self.t
        if name in _FUNCS:
            self._take("(")
            a = self._sum()
            self._take(",")
            b = self._sum()
            self._take(")")
            return _FUNCS[name](a, b)
        if op == "(":
            value = self._sum()
            self._take(")")
            return value
        raise AssertionError(f"unexpected token {(number, name, op)}")


def _terms(expr: str) -> list[str]:
    """Top-level ``+`` terms (the per-segment sum)."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(expr):
        depth += ch == "("
        depth -= ch == ")"
        if ch == "+" and depth == 0:
            out.append(expr[start:i])
            start = i + 1
    out.append(expr[start:])
    return out


def _active_terms(expr: str, t: float) -> int:
    """How many terms have an open gate (all lt/gte factors 1) at ``t``."""
    count = 0
    for term in _terms(expr):
        gates = re.findall(r"(?:lt|gte)\(t,[0-9.]+\)", term)
        assert gates, f"term without a time gate: {term}"
        if all(_Evaluator(g).evaluate(t) == 1.0 for g in gates):
            count += 1
    return count


def _x(expr: str, t: float) -> float:
    return _Evaluator(expr).evaluate(t)


SRC_W, CROP_W = 640, 202
MAX_X = SRC_W - CROP_W  # 438


# ── crop_x_expr ───────────────────────────────────────────────────────────────


def test_single_keyframe_is_a_constant():
    expr = crop_x_expr([(1.5, 219)], SRC_W, CROP_W)
    assert expr == "219"
    for t in (0.0, 1.5, 99.0):
        assert _x(expr, t) == 219


def test_equal_values_collapse_to_a_constant():
    assert (
        crop_x_expr([(0, 100.25), (2, 100.25), (5, 100.25)], SRC_W, CROP_W) == "100.25"
    )


TRACK = [(0.5, 40.0), (1.5, 400.0), (3.0, 100.0), (4.0, 100.0), (6.25, 300.0)]


def test_knots_hit_their_values_and_midpoints_interpolate():
    expr = crop_x_expr(TRACK, SRC_W, CROP_W)
    for t, x in TRACK:
        assert _x(expr, t) == pytest.approx(x, abs=1e-9)
    for (ta, xa), (tb, xb) in zip(TRACK, TRACK[1:]):
        assert _x(expr, (ta + tb) / 2) == pytest.approx((xa + xb) / 2, abs=1e-9)
        # a quarter of the way in: catches a slope applied from the wrong end
        assert _x(expr, ta + (tb - ta) / 4) == pytest.approx(
            xa + (xb - xa) / 4, abs=1e-9
        )


def test_times_outside_the_track_hold_the_end_values():
    expr = crop_x_expr(TRACK, SRC_W, CROP_W)
    for t in (0.0, 0.1, 0.4999):
        assert _x(expr, t) == 40.0
    for t in (6.25, 6.3, 1e4):
        assert _x(expr, t) == 300.0


def test_exactly_one_term_is_active_everywhere_including_knots():
    expr = crop_x_expr(TRACK, SRC_W, CROP_W)
    times = [t for t, _x in TRACK]
    probes = {0.0, 1e4, *times, *(t - 1e-6 for t in times), *(t + 1e-6 for t in times)}
    probes |= {(a + b) / 2 for a, b in zip(times, times[1:])}
    for t in sorted(probes):
        assert _active_terms(expr, t) == 1, f"t={t}"


def test_matches_numpy_interp_on_a_random_track():
    rng = random.Random(61)
    times = sorted(round(rng.uniform(0, 30), 3) for _ in range(40))
    track = [(t, rng.uniform(-50, MAX_X + 50)) for t in sorted(set(times))]
    expr = crop_x_expr(track, SRC_W, CROP_W)
    ts = np.array([t for t, _ in track])
    xs = np.clip([x for _, x in track], 0, MAX_X)
    for t in np.linspace(-1, 31, 641):
        assert _x(expr, float(t)) == pytest.approx(
            float(np.interp(t, ts, xs)), abs=2e-3
        )


def test_values_are_clamped_to_the_pan_range():
    expr = crop_x_expr([(0, -120), (1, 9999), (2, 200)], SRC_W, CROP_W)
    assert _x(expr, 0) == 0
    assert _x(expr, 1) == MAX_X
    assert _x(expr, 0.5) == MAX_X / 2  # interpolates between the CLAMPED knots
    assert _x(expr, 1.5) == pytest.approx((MAX_X + 200) / 2)
    samples = [_x(expr, t / 100) for t in range(-10, 260)]
    assert min(samples) == 0 and max(samples) == MAX_X


def test_upper_clamp_bound_is_src_minus_crop_width():
    expr = crop_x_expr([(0, 0), (1, 5000)], 1920, 608)
    assert _x(expr, 1) == 1920 - 608


def test_same_time_keyframes_make_a_hard_cut():
    expr = crop_x_expr([(0, 0), (2, 200), (2, 50), (4, 250)], SRC_W, CROP_W)
    assert "/0)" not in expr  # no zero-length segment term
    assert _x(expr, 1.0) == pytest.approx(100.0)
    assert _x(expr, 2.0 - 1e-6) == pytest.approx(200.0, abs=1e-3)
    assert _x(expr, 2.0) == 50.0
    assert _x(expr, 3.0) == pytest.approx(150.0)
    for t in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0):
        assert _active_terms(expr, t) == 1


def test_times_are_rounded_to_microseconds_before_printing():
    # knots that only differ below 1 us become a cut, never a 0-length divide
    expr = crop_x_expr([(0, 0), (1.0000001, 100), (1.0000002, 300)], SRC_W, CROP_W)
    assert _x(expr, 1.0) == 300.0
    assert _x(expr, 0.5) == pytest.approx(50.0)


@pytest.mark.parametrize(
    ("track", "match"),
    [
        ([], "1..64 keyframes"),
        ([(i * 0.1, 10.0 * i) for i in range(65)], "1..64 keyframes"),
        ([(1.0, 0), (0.5, 10)], "must not decrease"),
        ([(-0.1, 0)], "negative"),
        ([(0, float("nan"))], "not finite"),
        ([(float("inf"), 0)], "not finite"),
    ],
)
def test_invalid_tracks_are_rejected(track, match):
    with pytest.raises(ValueError, match=match):
        crop_x_expr(track, SRC_W, CROP_W)


@pytest.mark.parametrize("crop_w", [0, -2, SRC_W + 2])
def test_crop_width_must_fit_the_source(crop_w):
    with pytest.raises(ValueError, match="crop width"):
        crop_x_expr([(0, 0)], SRC_W, crop_w)


def test_sixty_four_keyframes_are_a_flat_filtergraph_safe_sum():
    track = [(i * 0.25, (i * 37) % 500) for i in range(64)]
    expr = crop_x_expr(track, SRC_W, CROP_W)
    assert len(_terms(expr)) == 65  # head hold + 63 segments + tail hold
    # safe inside x='...' in a filtergraph: no quote, option/chain separator,
    # pad label or escape (its commas are protected by the quotes)
    for ch in "':;[]\\":
        assert ch not in expr
    # flat: parenthesis depth stays tiny whatever the keyframe count
    depth = peak = 0
    for ch in expr:
        depth += ch == "("
        depth -= ch == ")"
        peak = max(peak, depth)
    assert peak <= 3


# ── pan window ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "window"),
    [
        ((640, 360), (202, 360, 0)),  # canvas 640x1137: 360*640/1137 = 202.6
        ((1920, 1080), (608, 1080, 0)),  # canvas 1920x3413: 607.6 -> 608
        ((1280, 720), (404, 720, 0)),  # canvas 1280x2275: 405.1 -> 405 -> 404
        ((1080, 1920), (1080, 1920, 0)),  # already 9:16: no pan room
        ((1080, 2400), (1080, 1920, 240)),  # taller: vertical centre crop
    ],
)
def test_pan_crop_is_the_largest_even_canvas_aspect_window(source, window):
    got = pan_crop(clip_service.reel_geometry(*source))
    assert (got.width, got.height, got.y) == window
    assert got.width % 2 == got.height % 2 == got.y % 2 == 0


# ── argv ──────────────────────────────────────────────────────────────────────

_FPS = Fraction(24000, 1001)


def _argv(crop_track, captions=True):
    track = (
        CaptionTrack("captions.ffconcat", "blank.png", {}, 0, 926, 640, 104)
        if captions
        else None
    )
    return render_service.build_reel_argv(
        "/src/in.mp4", "/out/reel.mp4", start=1.25, duration=3.5, fps=_FPS,
        geometry=clip_service.reel_geometry(640, 360),
        background_png="/w/background.png", captions=track, captions_dir="/w",
        crop_track=crop_track,
    )  # fmt: skip


def _graph(argv):
    return argv[argv.index("-filter_complex") + 1].split(";")


def test_crop_track_replaces_only_the_inset_chain():
    plain, moving = _argv(None), _argv([(0, 0), (3.5, 438)])
    expr = crop_x_expr([(0, 0), (3.5, 438)], 640, 202)
    assert _graph(moving) == [
        "[1:v]loop=loop=-1:size=1:start=0,settb=expr=1/24000,setpts=N*1001[bg]",
        f"[0:v]setpts=PTS-STARTPTS,crop=w=202:h=360:x='{expr}':y=0,scale=640:1137[in]",
        "[bg][in]overlay=x=0:y=0:ts_sync_mode=nearest[b]",
        "[b][2:v]overlay=x=0:y=926:eof_action=pass[c]",
        r"[c]crop=iw-mod(iw\,2):ih-mod(ih\,2):0:0,format=yuv420p[v]",
    ]
    # background grid, captions, even crop/yuv420p unchanged
    for i in (0, 3, 4):
        assert _graph(moving)[i] == _graph(plain)[i]
    # everything outside the filtergraph (trim, -map 0:a:0?, -r, codecs) too
    at = plain.index("-filter_complex") + 1
    assert moving[:at] + moving[at + 1 :] == plain[:at] + plain[at + 1 :]


def test_crop_track_none_is_the_letterbox_graph():
    assert _argv(None) == render_service.build_reel_argv(
        "/src/in.mp4", "/out/reel.mp4", start=1.25, duration=3.5, fps=_FPS,
        geometry=clip_service.reel_geometry(640, 360),
        background_png="/w/background.png",
        captions=CaptionTrack("captions.ffconcat", "blank.png", {}, 0, 926, 640, 104),
        captions_dir="/w",
    )  # fmt: skip
    assert "[0:v]setpts=PTS-STARTPTS,scale=640:-2[in]" in _graph(_argv(None))


def test_render_clip_passes_the_track_to_the_reel_graph(tmp_path):
    seen = []

    def fake_run(argv, **_):
        seen.append(argv)
        Path(argv[-1]).write_bytes(b"mp4")

    track = [(0.0, 10.0), (1.0, 300.0)]
    with (
        patch.object(render_service.ffmpeg_tools, "fps", return_value=_FPS),
        patch.object(
            render_service.ffmpeg_tools, "video_size", return_value=(640, 360)
        ),
        patch.object(
            render_service,
            "background_still",
            return_value=render_service.Image.new("RGB", (640, 1137)),
        ),
        patch.object(render_service.ffmpeg_tools, "run", side_effect=fake_run),
    ):
        render_service.render_clip(
            "/tmp/v.mp4", str(tmp_path / "o.mp4"), 0.0, 1.0,
            word_timings=[], crop_track=track,
        )  # fmt: skip
    graph = _graph(seen[0])
    assert f"crop=w=202:h=360:x='{crop_x_expr(track, 640, 202)}':y=0" in graph[1]


def test_render_clip_rejects_a_track_on_a_plain_trim(tmp_path):
    with (
        patch.object(render_service.ffmpeg_tools, "fps", return_value=_FPS),
        patch.object(render_service.ffmpeg_tools, "run") as run,
        pytest.raises(ValueError, match="crop_track needs a reel render"),
    ):
        render_service.render_clip(
            "/tmp/v.mp4", str(tmp_path / "o.mp4"), 0.0, 1.0, crop_track=[(0, 0)]
        )
    run.assert_not_called()
