"""B-roll inserts for the one-pass renderer: the pure parts.

Validation, the extra inputs and the filtergraph text. The overlay's enable
gate is checked by evaluating the generated ffmpeg expression at every output
frame time with a tiny independent evaluator (ffmpeg semantics for the
comparison subset), against a brute-force exact-rational oracle of which
frames an insert covers: frame k (shown at k/FPS) iff start <= k/FPS <
start + duration. Real-ffmpeg renders live in ``tests/e2e/test_render_broll.py``.
"""

from __future__ import annotations

import math
import random
import re
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import clip_service, render_service
from app.services.caption_track import CaptionTrack
from app.services.render_service import MAX_BROLL_INSERTS, BrollInsert, validate_broll

_FPS = Fraction(24000, 1001)
GEOMETRY = clip_service.reel_geometry(640, 360)  # canvas 640x1137
CAPTIONS = CaptionTrack("captions.ffconcat", "blank.png", {}, 0, 926, 640, 104)
_UNSET = object()


def _argv(broll=_UNSET, *, captions=True, crop_track=None, duration=3.5):
    kwargs = {} if broll is _UNSET else {"broll": broll}
    return render_service.build_reel_argv(
        "/src/in.mp4", "/out/reel.mp4", start=1.25, duration=duration, fps=_FPS,
        geometry=GEOMETRY, background_png="/w/background.png",
        captions=CAPTIONS if captions else None, captions_dir="/w",
        crop_track=crop_track, **kwargs,
    )  # fmt: skip


def _graph(argv) -> list[str]:
    return argv[argv.index("-filter_complex") + 1].split(";")


def _covered(start: float, duration: float, frames: int) -> set[int]:
    """Oracle: output frames k with start <= k/FPS < start + duration (exact)."""
    t0, t1 = Fraction(str(start)), Fraction(str(start)) + Fraction(str(duration))
    return {k for k in range(frames) if t0 <= k / _FPS < t1}


# ── a minimal evaluator for comparison expressions over ``t`` ────────────────

_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_]+)|(.))")
_FUNCS = {
    "lt": lambda a, b: float(a < b),
    "lte": lambda a, b: float(a <= b),
    "gt": lambda a, b: float(a > b),
    "gte": lambda a, b: float(a >= b),
    "between": lambda x, lo, hi: float(lo <= x <= hi),  # ffmpeg: inclusive
}


class _Evaluator:
    """Recursive descent over numbers, ``t``, ``+ - * /``, parentheses and the
    comparison functions above (ffmpeg semantics)."""

    def __init__(self, expr: str):
        self.tokens = [m.groups() for m in _TOKEN.finditer(expr) if m.group(0).strip()]

    def evaluate(self, t: float) -> float:
        self.t, self.pos = t, 0
        value = self._sum()
        assert self.pos == len(self.tokens), "trailing tokens"
        return value

    def _peek(self):
        return (
            self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None, None)
        )

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
            assert self._peek()[2] == "("
            self.pos += 1
            args = [self._sum()]
            while self._peek()[2] == ",":
                self.pos += 1
                args.append(self._sum())
            assert self._peek()[2] == ")"
            self.pos += 1
            return _FUNCS[name](*args)
        if op == "(":
            value = self._sum()
            assert self._peek()[2] == ")"
            self.pos += 1
            return value
        raise AssertionError(f"unexpected token {(number, name, op)}")


def _overlays(graph: list[str]) -> list[str]:
    """The B-roll overlay filters, in graph order."""
    return [f for f in graph if re.match(r"\[[^\]]+\]\[br\d+\]overlay=", f)]


def _shown(overlay: str, frames: int) -> set[int]:
    """Output frames on which ``overlay``'s enable gate is on.

    ``t`` is computed the way ffmpeg does for the overlay's main input: the
    frame's pts on the background's 1/24000 settb grid times the time base.
    """
    gate = re.search(r":enable='([^']*)'", overlay)
    assert gate, f"no enable gate in {overlay}"
    expr = _Evaluator(gate.group(1))
    return {
        k for k in range(frames) if abs(expr.evaluate(k * 1001 * (1 / 24000))) >= 0.5
    }


# ── validation ────────────────────────────────────────────────────────────────


@pytest.fixture
def clips(tmp_path) -> list[str]:
    paths = []
    for i in range(MAX_BROLL_INSERTS + 1):
        path = tmp_path / f"insert{i}.mp4"
        path.write_bytes(b"x")
        paths.append(str(path))
    return paths


def test_max_inserts_is_four():
    assert MAX_BROLL_INSERTS == 4


@pytest.mark.parametrize("empty", [None, [], ()])
def test_no_inserts_validate_to_an_empty_tuple(empty):
    assert validate_broll(empty, 5.0) == ()


def test_unsorted_inserts_are_accepted_and_sorted(clips):
    a, b, c = (
        BrollInsert(clips[0], 4.0, 1.0),
        BrollInsert(clips[1], 0.5, 1.0),
        BrollInsert(clips[2], 2.0, 1.5),
    )
    assert validate_broll([a, b, c], 6.0) == (b, c, a)


def test_touching_inserts_are_allowed(clips):
    """Half-open windows: [1, 2) and [2, 3) share no frame."""
    inserts = [BrollInsert(clips[0], 1.0, 1.0), BrollInsert(clips[1], 2.0, 1.0)]
    assert validate_broll(inserts, 3.0) == tuple(inserts)


def test_insert_may_end_exactly_at_the_clip_end(clips):
    # 0.1 + 0.2 != 0.3 in binary floating point: compared at microseconds
    insert = BrollInsert(clips[0], 0.1, 0.2)
    assert validate_broll([insert], 0.3) == (insert,)


def test_four_inserts_are_accepted(clips):
    inserts = [BrollInsert(clips[i], i * 1.0, 0.5) for i in range(4)]
    assert len(validate_broll(inserts, 4.0)) == 4


@pytest.mark.parametrize(
    ("windows", "clip_duration", "match"),
    [
        ([(0.0, 0.0)], 5.0, "duration must be > 0"),
        ([(1.0, -0.5)], 5.0, "duration must be > 0"),
        ([(-0.1, 1.0)], 5.0, "start must be >= 0"),
        ([(4.5, 1.0)], 5.0, "past the clip end"),
        ([(float("nan"), 1.0)], 5.0, "not finite"),
        ([(1.0, float("inf"))], 5.0, "not finite"),
        ([(1.0, 2.0), (2.5, 1.0)], 5.0, "overlap"),
        ([(2.5, 1.0), (1.0, 2.0)], 5.0, "overlap"),  # caught after sorting
        ([(1.0, 1.0), (1.0, 0.5)], 5.0, "overlap"),  # same start
        ([(i * 1.0, 0.5) for i in range(5)], 5.0, "at most 4"),
    ],
)
def test_invalid_inserts_are_rejected(clips, windows, clip_duration, match):
    inserts = [BrollInsert(clips[i], s, d) for i, (s, d) in enumerate(windows)]
    with pytest.raises(ValueError, match=match):
        validate_broll(inserts, clip_duration)


def test_missing_insert_file_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        validate_broll([BrollInsert(str(tmp_path / "nope.mp4"), 0.0, 1.0)], 5.0)


def test_directory_is_not_an_insert_file(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        validate_broll([BrollInsert(str(tmp_path), 0.0, 1.0)], 5.0)


def test_argv_builder_validates_timing_without_touching_files():
    """``build_reel_argv`` stays pure: same timing rules, no file check."""
    with pytest.raises(ValueError, match="overlap"):
        _argv([BrollInsert("/b/a.mp4", 1.0, 2.0), BrollInsert("/b/b.mp4", 2.0, 1.0)])
    _argv([BrollInsert("/no/such/file.mp4", 1.0, 1.0)])  # no file check


# ── byte-identical without B-roll ─────────────────────────────────────────────

_GOLDEN_CAPTIONED = [
    "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
    "-ss", "1.250000", "-t", "3.500000", "-i", "/src/in.mp4",
    "-i", "/w/background.png",
    "-f", "concat", "-safe", "0", "-i", "/w/captions.ffconcat",
    "-filter_complex",
    "[1:v]loop=loop=-1:size=1:start=0,settb=expr=1/24000,setpts=N*1001[bg];"
    "[0:v]setpts=PTS-STARTPTS,scale=640:-2[in];"
    "[bg][in]overlay=x=0:y=388:ts_sync_mode=nearest[b];"
    "[b][2:v]overlay=x=0:y=926:eof_action=pass[c];"
    r"[c]crop=iw-mod(iw\,2):ih-mod(ih\,2):0:0,format=yuv420p[v]",
    "-map", "[v]", "-map", "0:a:0?",
    "-t", "3.500000", "-r", "24000/1001",
    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
    "-threads", "<threads>", "-c:a", "aac",
    "/out/reel.mp4",
]  # fmt: skip

_GOLDEN_UNCAPTIONED = [
    "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
    "-ss", "1.250000", "-t", "3.500000", "-i", "/src/in.mp4",
    "-i", "/w/background.png",
    "-filter_complex",
    "[1:v]loop=loop=-1:size=1:start=0,settb=expr=1/24000,setpts=N*1001[bg];"
    "[0:v]setpts=PTS-STARTPTS,scale=640:-2[in];"
    "[bg][in]overlay=x=0:y=388:ts_sync_mode=nearest[b];"
    r"[b]crop=iw-mod(iw\,2):ih-mod(ih\,2):0:0,format=yuv420p[v]",
    "-map", "[v]", "-map", "0:a:0?",
    "-t", "3.500000", "-r", "24000/1001",
    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
    "-threads", "<threads>", "-c:a", "aac",
    "/out/reel.mp4",
]  # fmt: skip


def _golden(golden: list[str]) -> list[str]:
    threads = str(render_service._threads())
    return [threads if a == "<threads>" else a for a in golden]


@pytest.mark.parametrize("broll", [_UNSET, None, [], ()])
@pytest.mark.parametrize(
    ("captions", "golden"),
    [(True, _GOLDEN_CAPTIONED), (False, _GOLDEN_UNCAPTIONED)],
)
def test_no_broll_argv_is_byte_identical_to_the_golden(broll, captions, golden):
    assert _argv(broll, captions=captions) == _golden(golden)


# ── inputs and filtergraph ────────────────────────────────────────────────────

ONE = BrollInsert("/b/one.mp4", 2.0, 1.0)
TWO = BrollInsert("/b/two.mp4", 5.0, 1.0)
DURATION = 7.0
FRAMES = math.ceil(DURATION * _FPS)  # 168 output frames
FIT = (
    "fps=24000/1001,"
    "scale=640:1137:force_original_aspect_ratio=increase,crop=640:1137,"
    "setsar=1,format=yuv420p"
)


def _insert_inputs(argv: list[str]) -> list[tuple[str, str, str]]:
    """``(-stream_loop value, -t value, path)`` per looped input, in order."""
    out = []
    for i, arg in enumerate(argv):
        if arg == "-stream_loop":
            assert argv[i + 2] == "-t" and argv[i + 4] == "-i", argv[i : i + 6]
            out.append((argv[i + 1], argv[i + 3], argv[i + 5]))
    return out


def test_each_insert_is_one_looped_input_after_the_captions():
    argv = _argv([TWO, ONE], duration=DURATION)  # unsorted on purpose
    assert argv.count("-stream_loop") == 2
    assert argv.count("-i") == 3 + 2  # source, background, captions + inserts
    assert [p for _loop, _t, p in _insert_inputs(argv)] == ["/b/one.mp4", "/b/two.mp4"]
    assert all(loop == "-1" for loop, _t, _p in _insert_inputs(argv))
    # the inserts come after the captions input, so the captions stay [2:v]
    concat = argv.index("/w/captions.ffconcat")
    assert all(argv.index(p) > concat for p in ("/b/one.mp4", "/b/two.mp4"))
    assert argv.index("-filter_complex") > argv.index("/b/two.mp4")


@pytest.mark.parametrize("insert", [ONE, TWO, BrollInsert("/b/x.mp4", 0.0, 0.3)])
def test_insert_input_length_is_the_covered_span(insert):
    argv = _argv([insert], duration=DURATION)
    [(_loop, length, _path)] = _insert_inputs(argv)
    covered = _covered(insert.start, insert.duration, FRAMES)
    span = Fraction(len(covered)) / _FPS
    # exactly the covered frames (printed to the microsecond): long enough
    # for every one of them, and bounded (the loop is infinite)
    assert abs(Fraction(length) - span) <= Fraction(1, 10**6)


def test_inserts_feed_their_own_offset_and_cover_fit_chain():
    graph = _graph(_argv([TWO, ONE], duration=DURATION))
    # first covered output frame: 48 (2.002 s) and 120 (5.005 s); the insert's
    # frame 0 is shifted there, on the clip's frame grid, then cover-fitted
    assert f"[3:v]setpts=PTS-STARTPTS+2.002/TB,{FIT}[br0]" in graph
    assert f"[4:v]setpts=PTS-STARTPTS+5.005/TB,{FIT}[br1]" in graph


def test_overlays_chain_in_order_between_the_composite_and_the_captions():
    graph = _graph(_argv([TWO, ONE], duration=DURATION))
    overlays = _overlays(graph)
    assert [o.split("overlay=")[0] for o in overlays] == ["[b][br0]", "[b0][br1]"]
    for overlay in overlays:
        assert overlay.startswith(overlay.split("overlay=")[0] + "overlay=x=0:y=0:")
        assert overlay.endswith(":eof_action=pass" + overlay[overlay.rindex("[") :])
    assert overlays[0].endswith("[b0]") and overlays[1].endswith("[b1]")
    captions = graph.index("[b1][2:v]overlay=x=0:y=926:eof_action=pass[c]")
    composite = graph.index("[bg][in]overlay=x=0:y=388:ts_sync_mode=nearest[b]")
    assert composite < graph.index(overlays[0]) < graph.index(overlays[1]) < captions
    assert graph[-1] == r"[c]crop=iw-mod(iw\,2):ih-mod(ih\,2):0:0,format=yuv420p[v]"


def test_overlays_feed_the_even_crop_without_captions():
    argv = _argv([ONE, TWO], captions=False, duration=DURATION)
    graph = _graph(argv)
    assert argv.count("-i") == 2 + 2
    assert f"[2:v]setpts=PTS-STARTPTS+2.002/TB,{FIT}[br0]" in graph
    assert f"[3:v]setpts=PTS-STARTPTS+5.005/TB,{FIT}[br1]" in graph
    assert [o.split("overlay=")[0] for o in _overlays(graph)] == [
        "[b][br0]",
        "[b0][br1]",
    ]
    assert graph[-1] == r"[b1]crop=iw-mod(iw\,2):ih-mod(ih\,2):0:0,format=yuv420p[v]"


def test_insert_audio_is_never_mapped_and_the_rest_of_the_argv_is_unchanged():
    plain = _argv(captions=True, duration=DURATION)
    argv = _argv([ONE, TWO], duration=DURATION)
    maps = [argv[i + 1] for i, a in enumerate(argv) if a == "-map"]
    assert maps == ["[v]", "0:a:0?"]
    # drop the insert inputs and the graph: what is left is the plain argv
    stripped = list(argv)
    for _ in range(2):
        at = stripped.index("-stream_loop")
        del stripped[at : at + 6]
    at = stripped.index("-filter_complex") + 1
    assert stripped[:at] + stripped[at + 1 :] == plain[:at] + plain[at + 1 :]
    # and the graph only gained the insert chains and overlays
    added = [f for f in _graph(argv) if f not in _graph(plain)]
    assert len(added) == 4 + 1  # 2 chains + 2 overlays + the re-pointed captions
    removed = [f for f in _graph(plain) if f not in _graph(argv)]
    assert removed == ["[b][2:v]overlay=x=0:y=926:eof_action=pass[c]"]


@pytest.mark.parametrize(
    ("start", "duration", "first", "end"),
    [
        (2.0, 1.0, 48, 72),  # 2.0 s and 3.0 s fall between frames
        (2.002, 1.001, 48, 72),  # exactly on frames 48 and 72: [48, 72)
        (0.0, 0.5, 0, 12),  # frame 0 at t=0 is covered
        (6.5, 0.5, 156, 168),  # ends at the clip end
        (2.97, 0.02, 72, 72),  # between frames 71 (2.961) and 72 (3.003): none
    ],
)
def test_enable_gate_covers_exactly_the_half_open_window(start, duration, first, end):
    argv = _argv([BrollInsert("/b/x.mp4", start, duration)], duration=DURATION)
    [overlay] = _overlays(_graph(argv))
    assert _covered(start, duration, FRAMES) == set(range(first, end))
    assert _shown(overlay, FRAMES) == set(range(first, end))


def test_enable_gates_match_the_oracle_on_random_inserts():
    rng = random.Random(712)
    for _ in range(40):
        cuts = sorted(round(rng.uniform(0, DURATION), 3) for _ in range(8))
        inserts = [
            BrollInsert(f"/b/{i}.mp4", cuts[2 * i], round(cuts[2 * i + 1] - cuts[2 * i], 3))
            for i in range(4)
            if cuts[2 * i + 1] > cuts[2 * i]
        ]
        overlays = _overlays(_graph(_argv(inserts, duration=DURATION)))
        assert len(overlays) == len(inserts)
        for insert, overlay in zip(inserts, overlays):
            assert _shown(overlay, FRAMES) == _covered(
                insert.start, insert.duration, FRAMES
            ), insert


def test_offset_lands_the_first_insert_frame_on_the_first_covered_frame():
    for start in (0.0, 0.01, 1.98, 2.0, 2.002, 4.4):
        argv = _argv([BrollInsert("/b/x.mp4", start, 1.0)], duration=DURATION)
        chain = next(f for f in _graph(argv) if f.endswith("[br0]"))
        offset = Fraction(re.search(r"STARTPTS\+([0-9.]+)/TB", chain).group(1))
        first = min(_covered(start, 1.0, FRAMES))
        # within a microsecond of the frame time: the fps filter rounds it
        # onto exactly that frame of the clip's grid
        assert abs(offset - first / _FPS) <= Fraction(1, 10**6), start


def test_broll_composes_with_a_crop_track():
    track = [(0.0, 0.0), (3.5, 438.0)]
    moving = _graph(_argv(crop_track=track, duration=DURATION))
    both = _graph(_argv([ONE], crop_track=track, duration=DURATION))
    pan = next(f for f in moving if f.startswith("[0:v]"))
    assert pan in both and "crop=w=202:h=360:x='" in pan
    assert "[bg][in]overlay=x=0:y=0:ts_sync_mode=nearest[b]" in both
    [overlay] = _overlays(both)
    assert overlay.startswith("[b][br0]overlay=x=0:y=0:")
    assert both.index(overlay) < both.index(
        "[b0][2:v]overlay=x=0:y=926:eof_action=pass[c]"
    )


def test_grid_rate_drives_the_insert_frame_rate():
    vfr = Fraction(576089600, 19266773)  # snapped by grid_rate
    argv = render_service.build_reel_argv(
        "/src/in.mp4", "/out/reel.mp4", start=0.0, duration=3.0, fps=vfr,
        geometry=GEOMETRY, background_png="/w/background.png", captions=None,
        captions_dir=None, broll=[BrollInsert("/b/x.mp4", 1.0, 1.0)],
    )  # fmt: skip
    rate = argv[argv.index("-r") + 1]
    assert rate == render_service._rate(render_service.grid_rate(vfr))
    chain = next(f for f in _graph(argv) if f.endswith("[br0]"))
    assert f",fps={rate}," in chain


# ── render_clip ───────────────────────────────────────────────────────────────


def _render(tmp_path, **kwargs) -> list[list[str]]:
    seen = []

    def fake_run(argv, **_):
        seen.append(argv)
        Path(argv[-1]).write_bytes(b"mp4")

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
            "/tmp/v.mp4", str(tmp_path / "o.mp4"), 1.0, 8.0, **kwargs
        )
    return seen


def _stable(argv: list[str]) -> list[str]:
    return [re.sub(r"reelsmith-render-[^/]+", "reelsmith-render-X", a) for a in argv]


def test_render_clip_passes_validated_sorted_inserts_to_the_graph(tmp_path, clips):
    one, two = BrollInsert(clips[0], 2.0, 1.0), BrollInsert(clips[1], 5.0, 1.0)
    [argv] = _render(tmp_path, word_timings=[], broll=[two, one])
    assert [p for _l, _t, p in _insert_inputs(argv)] == [clips[0], clips[1]]
    assert len(_overlays(_graph(argv))) == 2


@pytest.mark.parametrize("empty", [None, []])
def test_render_clip_without_broll_runs_the_unchanged_argv(tmp_path, empty):
    [plain] = _render(tmp_path / "a", word_timings=[])
    [argv] = _render(tmp_path / "b", word_timings=[], broll=empty)
    # the work dir and the output's temp name differ per run
    assert _stable(argv)[:-1] == _stable(plain)[:-1]
    assert "-stream_loop" not in argv


def test_render_clip_validates_before_any_work(tmp_path):
    missing = BrollInsert(str(tmp_path / "missing.mp4"), 1.0, 1.0)
    with (
        patch.object(render_service.ffmpeg_tools, "fps", return_value=_FPS),
        patch.object(render_service, "background_still") as still,
        patch.object(render_service.ffmpeg_tools, "run") as run,
        pytest.raises(ValueError, match="does not exist"),
    ):
        render_service.render_clip(
            "/tmp/v.mp4", str(tmp_path / "o.mp4"), 0.0, 4.0,
            word_timings=[], broll=[missing],
        )  # fmt: skip
    still.assert_not_called()
    run.assert_not_called()


def test_render_clip_rejects_inserts_past_the_clip_end(tmp_path, clips):
    with (
        patch.object(render_service.ffmpeg_tools, "fps", return_value=_FPS),
        patch.object(render_service.ffmpeg_tools, "run") as run,
        pytest.raises(ValueError, match="past the clip end"),
    ):
        render_service.render_clip(
            "/tmp/v.mp4", str(tmp_path / "o.mp4"), 10.0, 12.0,
            word_timings=[], broll=[BrollInsert(clips[0], 1.5, 1.0)],
        )  # fmt: skip
    run.assert_not_called()


def test_render_clip_rejects_broll_on_a_plain_trim(tmp_path, clips):
    with (
        patch.object(render_service.ffmpeg_tools, "fps", return_value=_FPS),
        patch.object(render_service.ffmpeg_tools, "run") as run,
        pytest.raises(ValueError, match="broll needs a reel render"),
    ):
        render_service.render_clip(
            "/tmp/v.mp4", str(tmp_path / "o.mp4"), 0.0, 4.0,
            broll=[BrollInsert(clips[0], 1.0, 1.0)],
        )  # fmt: skip
    run.assert_not_called()
