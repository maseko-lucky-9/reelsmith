"""B-roll providers (FR-010, T012): the local library, provider choice and the
bounded fetch the orchestrator uses.

The local library is ``settings.broll_library_dir``: ``*.mp4`` files named
by keyword (``ocean_waves.mp4`` answers "ocean" and "waves").
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

import pytest

from app.services import broll_service
from app.services.broll_service import (
    BrollAsset,
    LocalBRoll,
    NoBRoll,
    fetch_all,
    get_broll_provider,
    get_broll_service,
)
from app.settings import settings

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.mp4"


@pytest.fixture
def library(tmp_path, monkeypatch) -> Path:
    lib = tmp_path / "broll"
    lib.mkdir()
    monkeypatch.setattr(settings, "broll_library_dir", str(lib))
    return lib


def _touch(lib: Path, *names: str) -> None:
    for name in names:
        (lib / name).write_bytes(b"\x00")


def test_no_broll_returns_none():
    svc = NoBRoll()
    assert svc.find_broll("anything") is None


def test_get_broll_service_none_mode(monkeypatch):
    monkeypatch.setattr(settings, "broll_provider", "none")
    assert isinstance(get_broll_service(), NoBRoll)


def test_get_broll_service_local_mode(monkeypatch):
    monkeypatch.setattr(settings, "broll_provider", "local")
    assert isinstance(get_broll_service(), LocalBRoll)


def test_local_broll_no_clips_returns_none(library):
    svc = LocalBRoll()
    assert svc.find_broll("A beautiful sunset over the mountains") is None


def test_local_broll_finds_matching_clip(library, monkeypatch):
    _touch(library, "sunset_timelapse.mp4")
    svc = LocalBRoll()
    monkeypatch.setattr(svc, "_extract_noun_phrases", lambda text: ["sunset"])
    result = svc.find_broll("The beautiful sunset over the mountains was amazing")
    assert result is not None
    assert "sunset" in result


def test_local_broll_extract_noun_phrases_fallback(monkeypatch):
    svc = LocalBRoll()
    phrases = svc._extract_noun_phrases(
        "John Smith is talking about Machine Learning today"
    )
    assert isinstance(phrases, list)


# ── the lowercase-query fix ───────────────────────────────────────────────────


def test_a_lowercase_query_finds_its_clip(library):
    """The planner's queries are lowercase; the old Capitalised-words-only
    fallback returned None for every one of them."""
    _touch(library, "wikipedia_editing.mp4")

    assert LocalBRoll().find_broll("wikipedia") == str(
        library / "wikipedia_editing.mp4"
    )


def test_matching_ignores_case_on_both_sides(library):
    _touch(library, "Mountain_Drone.MP4")

    assert LocalBRoll().find_broll("MOUNTAIN") == str(library / "Mountain_Drone.MP4")


def test_a_plural_query_finds_the_singular_file(library):
    _touch(library, "mountain_drone.mp4")

    assert LocalBRoll().find_broll("mountains") == str(library / "mountain_drone.mp4")


@pytest.mark.parametrize(
    ("query", "name"),
    [("process", "process_flow.mp4"), ("glasses", "glasses_closeup.mp4"), ("bus", "bus_stop.mp4")],
)
def test_words_ending_in_s_match_their_own_file(library, query, name):
    _touch(library, name)

    assert LocalBRoll().find_broll(query) == str(library / name)


def test_keywords_match_whole_file_name_words_not_substrings(library):
    _touch(library, "street_night.mp4", "scary_forest.mp4")

    assert LocalBRoll().find_broll("tree") is None
    assert LocalBRoll().find_broll("car") is None


def test_stopwords_never_match(library):
    _touch(library, "the_end.mp4")

    assert LocalBRoll().find_broll("the") is None


def test_free_text_tries_its_words_in_spoken_order(library):
    _touch(library, "dog_running.mp4", "city_traffic.mp4")

    found = LocalBRoll().find_broll("a busy city and a dog")

    assert found == str(library / "city_traffic.mp4")


def test_several_matching_files_pick_the_first_by_name(library):
    _touch(library, "ocean_b.mp4", "ocean_a.mp4", "ocean_c.mp4")

    assert LocalBRoll().find_broll("ocean") == str(library / "ocean_a.mp4")


def test_only_mp4_files_count(library):
    (library / "ocean.mp4.d").mkdir()
    _touch(library, "ocean.mov", "ocean.txt")

    assert LocalBRoll().find_broll("ocean") is None


def test_a_missing_library_finds_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "broll_library_dir", str(tmp_path / "nope"))

    assert LocalBRoll().find_broll("ocean") is None


def test_the_library_dir_is_read_at_call_time(tmp_path, monkeypatch):
    svc = LocalBRoll()
    for name in ("a", "b"):
        lib = tmp_path / name
        lib.mkdir()
        _touch(lib, f"ocean_{name}.mp4")
        monkeypatch.setattr(settings, "broll_library_dir", str(lib))

        assert svc.find_broll("ocean") == str(lib / f"ocean_{name}.mp4")


# ── provider choice ───────────────────────────────────────────────────────────


def test_the_default_provider_is_none():
    assert type(settings).model_fields["broll_provider"].default == "none"


@pytest.mark.parametrize("name", ["local", "pexels"])
def test_known_providers(name):
    assert get_broll_provider(name).name == name


@pytest.mark.parametrize("name", ["none", "", "Local", "pexel"])
def test_none_and_unknown_names_are_not_providers(name):
    with pytest.raises(ValueError, match="B-roll provider"):
        get_broll_provider(name)


async def test_the_local_provider_returns_an_asset(library):
    shutil.copy(SAMPLE, library / "ocean_waves.mp4")

    asset = await get_broll_provider("local").fetch("ocean")

    assert asset == BrollAsset(
        path=str(library / "ocean_waves.mp4"),
        provider="local",
        asset_id="ocean_waves.mp4",
        author="",
        source_url="",
    )


async def test_the_local_provider_returns_none_without_a_match(library):
    assert await get_broll_provider("local").fetch("ocean") is None


async def test_the_pexels_provider_calls_fetch_asset(monkeypatch):
    seen = []

    async def _fetch(query):
        seen.append(query)
        return None

    monkeypatch.setattr("app.services.broll_pexels_service.fetch_asset", _fetch)

    assert await get_broll_provider("pexels").fetch("ocean") is None
    assert seen == ["ocean"]


# ── fetch_all ─────────────────────────────────────────────────────────────────


class _Provider:
    name = "fake"

    def __init__(self, assets, *, gate: asyncio.Event | None = None):
        self.assets = assets
        self.gate = gate
        self.in_flight = 0
        self.peak = 0
        self.calls: list[str] = []

    async def fetch(self, query):
        self.calls.append(query)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            if self.gate is not None:
                await self.gate.wait()
            result = self.assets[query]
            if isinstance(result, BaseException):
                raise result
            return result
        finally:
            self.in_flight -= 1


def _asset(path: Path, asset_id: str = "1") -> BrollAsset:
    return BrollAsset(str(path), "fake", asset_id, "Ann", "https://example.test/1")


@pytest.fixture
def clip(tmp_path) -> Path:
    path = tmp_path / "clip.mp4"
    shutil.copy(SAMPLE, path)
    return path


async def test_fetch_all_keeps_the_query_order(clip):
    a, b = _asset(clip, "a"), _asset(clip, "b")
    provider = _Provider({"ocean": a, "forest": b})

    assert await fetch_all(provider, ["ocean", "forest"]) == [a, b]


async def test_fetch_all_runs_at_most_two_fetches_at_once(clip):
    queries = [f"q{i}" for i in range(5)]
    provider = _Provider({q: _asset(clip, q) for q in queries})

    found = await fetch_all(provider, queries)

    assert provider.peak == 2
    assert [a.asset_id for a in found] == queries


async def test_fetch_all_drops_a_failed_query(clip, caplog):
    good = _asset(clip)
    provider = _Provider({"ocean": RuntimeError("boom"), "forest": good, "tree": None})

    with caplog.at_level(logging.WARNING, logger=broll_service.__name__):
        found = await fetch_all(provider, ["ocean", "forest", "tree"])

    assert found == [None, good, None]
    assert "ocean" in caplog.text and "boom" in caplog.text


async def test_fetch_all_drops_an_asset_that_is_not_a_video(tmp_path, clip):
    fake_video = tmp_path / "html.mp4"
    fake_video.write_text("<html>not a video</html>")
    provider = _Provider(
        {
            "ocean": _asset(fake_video),
            "forest": _asset(tmp_path / "gone.mp4"),
            "tree": _asset(clip),
        }
    )

    found = await fetch_all(provider, ["ocean", "forest", "tree"])

    assert found == [None, None, _asset(clip)]


async def test_fetch_all_lets_cancellation_through(clip):
    gate = asyncio.Event()
    provider = _Provider({"ocean": _asset(clip)}, gate=gate)
    task = asyncio.create_task(fetch_all(provider, ["ocean"]))
    while not provider.calls:
        await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_fetch_all_never_swallows_a_cancelled_error_from_the_provider(clip):
    """A CancelledError is never "a failed query": even raised by the provider
    itself (no outer cancel, so gather would not re-raise it), it propagates."""
    provider = _Provider({"ocean": asyncio.CancelledError(), "forest": _asset(clip)})

    with pytest.raises(asyncio.CancelledError):
        await fetch_all(provider, ["ocean", "forest"])


async def test_fetch_all_of_nothing_is_empty():
    assert await fetch_all(_Provider({}), []) == []
