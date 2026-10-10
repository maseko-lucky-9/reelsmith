"""Hardened deletion for the retention sweeps (T033, security review).

Every delete is re-validated at the moment it happens, not trusted from the
sweep's earlier check: the entry must still be below a managed root, reached
through real folders only (a folder swapped for a symlink is refused), and
must not itself be a symlink. On platforms with ``dir_fd`` support (macOS,
Linux) the folder chain is opened from the root with ``O_NOFOLLOW`` and the
entry is removed relative to that pinned folder; elsewhere the parent is
re-resolved and compared. Both modes are tested by switching
``retention._PINNED``.

Paths read from the database are untrusted: a NUL byte, a relative path or a
``..`` component is rejected before any filesystem call.
"""

from __future__ import annotations

import logging
import os
import pathlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from app.services import retention
from app.services.retention import (
    delete_below_root,
    sweep_retired_files,
    sweep_unused_sources,
)

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=31)


def _never(_job_id: str) -> bool:
    return False


def _file(path: Path, data: bytes = b"x", *, age: timedelta | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if age is not None:
        ts = (NOW - age).timestamp()
        os.utime(path, (ts, ts))
    return path


@pytest.fixture(params=["pinned", "by_path"])
def mode(request, monkeypatch) -> str:
    """Run the test with the dir_fd walk and with the re-resolve fallback."""
    monkeypatch.setattr(retention, "_PINNED", request.param == "pinned")
    return request.param


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = (tmp_path / "downloads").resolve()
    (path / "uploads").mkdir(parents=True)
    return path


@pytest.fixture
def roots(root: Path) -> tuple[Path, ...]:
    return (root, root / "uploads")


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    path = (tmp_path / "outside").resolve()
    path.mkdir()
    return path


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _add_job(factory, video_path: str | None) -> str:
    async with factory() as session:
        job = JobRecord(
            youtube_url="https://x.test",
            status="completed",
            video_path=video_path,
            created_at=OLD,
            updated_at=OLD,
        )
        session.add(job)
        await session.commit()
        return job.id


async def _add_clip(factory, job_id: str, output_path: str | None, *, retired=True):
    async with factory() as session:
        clip = ClipRecord(
            job_id=job_id,
            retired=retired,
            output_path=output_path,
            created_at=OLD,
            updated_at=OLD,
        )
        session.add(clip)
        await session.commit()
        return clip.id


async def _video_path(factory, job_id: str) -> str | None:
    async with factory() as session:
        return (await session.get(JobRecord, job_id)).video_path


async def _sweep_sources(factory, roots) -> list[str]:
    return await sweep_unused_sources(
        factory, retention_days=30, now=NOW, roots=roots, in_flight=_never
    )


async def _sweep_files(factory, roots) -> list[str]:
    return await sweep_retired_files(
        factory, grace_hours=24, now=NOW, roots=roots, in_flight=_never
    )


# ── The primitive ─────────────────────────────────────────────────────────────


def test_a_regular_file_below_a_root_is_deleted(mode, root, roots):
    target = _file(root / "job" / "clips" / "00.mp4")

    assert delete_below_root(target, roots, owner="t") is True

    assert not target.exists()


def test_a_missing_file_is_not_an_error(mode, root, roots, caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.retention"):
        assert delete_below_root(root / "job" / "gone.mp4", roots, owner="t") is False

    assert caplog.records == []


def test_a_file_swapped_for_a_symlink_is_refused(mode, root, roots, outside, caplog):
    secret = _file(outside / "secret.mp4", b"secret")
    link = root / "job" / "00.mp4"
    link.parent.mkdir()
    link.symlink_to(secret)

    with caplog.at_level(logging.WARNING, logger="app.services.retention"):
        assert delete_below_root(link, roots, owner="t") is False

    assert secret.read_bytes() == b"secret"
    assert link.is_symlink()
    assert any("refused" in r.getMessage() for r in caplog.records)


def test_a_folder_swapped_for_a_symlink_is_refused(mode, root, roots, outside):
    victim = _file(outside / "elsewhere" / "00.mp4", b"victim")
    (root / "job").symlink_to(victim.parent, target_is_directory=True)

    assert delete_below_root(root / "job" / "00.mp4", roots, owner="t") is False

    assert victim.read_bytes() == b"victim"


def test_an_empty_folder_behind_a_swapped_folder_is_not_removed(
    mode, root, roots, outside
):
    victim = outside / "elsewhere" / "clips"
    victim.mkdir(parents=True)
    (root / "job").symlink_to(victim.parent, target_is_directory=True)

    assert (
        delete_below_root(root / "job" / "clips", roots, owner="t", directory=True)
        is False
    )

    assert victim.is_dir()


def test_a_path_outside_every_root_is_refused(mode, roots, outside):
    target = _file(outside / "00.mp4")

    assert delete_below_root(target, roots, owner="t") is False

    assert target.exists()


def test_a_managed_root_itself_is_never_removed(mode, root, roots):
    assert (
        delete_below_root(root / "uploads", roots, owner="t", directory=True) is False
    )

    assert (root / "uploads").is_dir()


def test_a_relative_path_is_refused(mode, root, roots, monkeypatch):
    target = _file(root / "job" / "00.mp4")
    monkeypatch.chdir(root)

    assert delete_below_root(Path("job/00.mp4"), roots, owner="t") is False

    assert target.exists()


def test_a_folder_that_is_not_empty_is_kept(mode, root, roots):
    keep = _file(root / "job" / "notes.txt", b"mine")

    assert delete_below_root(root / "job", roots, owner="t", directory=True) is False

    assert keep.read_bytes() == b"mine"


def test_an_empty_folder_is_removed(mode, root, roots):
    (root / "job" / "clips").mkdir(parents=True)

    assert delete_below_root(root / "job" / "clips", roots, owner="t", directory=True)

    assert not (root / "job" / "clips").exists()
    assert (root / "job").is_dir()


def test_accept_sees_the_entry_it_is_about_to_delete(mode, root, roots):
    young = _file(root / "job" / "00.mp4", age=timedelta(hours=1))
    cutoff = (NOW - timedelta(hours=24)).timestamp()

    assert (
        delete_below_root(
            young, roots, owner="t", accept=lambda st: st.st_mtime < cutoff
        )
        is False
    )

    assert young.exists()


# ── Swaps between a sweep's check and its delete ──────────────────────────────


def _swap_after_check(monkeypatch, target: Path, swap) -> None:
    """Run ``swap`` right after the sweep's own managed-root check of
    ``target`` (the validation step), i.e. before the delete."""
    real = retention._is_managed
    done = False

    def _checked_then_swapped(path, roots):
        nonlocal done
        result = real(path, roots)
        if path == target and not done:
            done = True
            swap()
        return result

    monkeypatch.setattr(retention, "_is_managed", _checked_then_swapped)


def _replace_with_symlink(path: Path, target: Path) -> None:
    path.unlink()
    path.symlink_to(target)


def _replace_folder_with_symlink(folder: Path, target: Path) -> None:
    folder.rename(folder.with_name(folder.name + ".moved"))
    folder.symlink_to(target, target_is_directory=True)


async def test_source_swapped_for_a_symlink_after_the_check_is_not_followed(
    mode, factory, root, roots, outside, monkeypatch
):
    source = _file(root / "Talk-abcdef12" / "Talk.mp4", b"source")
    secret = _file(outside / "secret.mp4", b"secret")
    job_id = await _add_job(factory, str(source))
    _swap_after_check(
        monkeypatch, source, lambda: _replace_with_symlink(source, secret)
    )

    await _sweep_sources(factory, roots)

    assert secret.read_bytes() == b"secret"
    assert source.is_symlink()
    assert job_id


async def test_source_folder_swapped_for_a_symlink_after_the_check_is_not_followed(
    mode, factory, root, roots, outside, monkeypatch
):
    folder = root / "Talk-abcdef12"
    source = _file(folder / "Talk.mp4", b"source")
    (folder / "clips").mkdir()
    elsewhere = outside / "elsewhere"
    victim = _file(elsewhere / "Talk.mp4", b"victim")
    victim_sidecar = _file(elsewhere / "Talk.words.json", b"[]")
    (elsewhere / "clips").mkdir()
    await _add_job(factory, str(source))
    _swap_after_check(
        monkeypatch, source, lambda: _replace_folder_with_symlink(folder, elsewhere)
    )

    await _sweep_sources(factory, roots)

    assert victim.read_bytes() == b"victim"
    assert victim_sidecar.exists()
    assert (elsewhere / "clips").is_dir()
    assert elsewhere.is_dir()


async def test_retired_file_swapped_for_a_symlink_after_the_check_is_not_followed(
    mode, factory, root, roots, outside, monkeypatch
):
    mp4 = _file(root / "c" / "00.mp4", age=timedelta(days=2))
    secret = _file(outside / "secret.mp4", b"secret", age=timedelta(days=2))
    await _add_clip(factory, await _add_job(factory, None), str(mp4))
    _swap_after_check(monkeypatch, mp4, lambda: _replace_with_symlink(mp4, secret))

    assert await _sweep_files(factory, roots) == []

    assert secret.read_bytes() == b"secret"
    assert mp4.is_symlink()


async def test_retired_file_folder_swapped_for_a_symlink_after_the_check(
    mode, factory, root, roots, outside, monkeypatch
):
    mp4 = _file(root / "c" / "00.mp4", age=timedelta(days=2))
    victim = _file(outside / "elsewhere" / "00.mp4", b"victim", age=timedelta(days=2))
    await _add_clip(factory, await _add_job(factory, None), str(mp4))
    _swap_after_check(
        monkeypatch,
        mp4,
        lambda: _replace_folder_with_symlink(mp4.parent, victim.parent),
    )

    assert await _sweep_files(factory, roots) == []

    assert victim.read_bytes() == b"victim"


# ── Untrusted database paths ──────────────────────────────────────────────────


@pytest.fixture
def fs_spy(monkeypatch):
    """Record every resolve/unlink/rmdir the sweeps make (calls go through)."""
    calls: list[tuple[str, str]] = []

    def _spy(name, real):
        def _wrapper(*args, **kwargs):
            calls.append((name, str(args[0])))
            return real(*args, **kwargs)

        return _wrapper

    monkeypatch.setattr(pathlib.Path, "resolve", _spy("resolve", pathlib.Path.resolve))
    monkeypatch.setattr(pathlib.Path, "unlink", _spy("unlink", pathlib.Path.unlink))
    monkeypatch.setattr(pathlib.Path, "rmdir", _spy("rmdir", pathlib.Path.rmdir))
    monkeypatch.setattr(os, "unlink", _spy("unlink", os.unlink))
    monkeypatch.setattr(os, "rmdir", _spy("rmdir", os.rmdir))
    return calls


def _unsafe_paths(root: Path) -> dict[str, str]:
    """DB values that point at real files below the root, spelled unsafely."""
    return {
        "dotdot": f"{root}/Other-11111111/../Talk-abcdef12/Talk.mp4",
        "relative": "Talk-abcdef12/Talk.mp4",
        "nul": f"{root}/Talk-abcdef12/Talk.mp4\x00.txt",
    }


@pytest.mark.parametrize("kind", ["dotdot", "relative", "nul"])
async def test_unsafe_source_paths_are_rejected_before_any_filesystem_call(
    factory, root, roots, monkeypatch, fs_spy, kind
):
    source = _file(root / "Talk-abcdef12" / "Talk.mp4", b"source")
    (root / "Other-11111111").mkdir()
    monkeypatch.chdir(root)
    raw = _unsafe_paths(root)[kind]
    job_id = await _add_job(factory, raw)
    fs_spy.clear()

    assert await _sweep_sources(factory, roots) == []

    assert source.read_bytes() == b"source"
    assert await _video_path(factory, job_id) == raw
    assert [c for c in fs_spy if c[0] in ("unlink", "rmdir")] == []
    assert not any(
        "\x00" in arg or ".." in Path(arg).parts or not os.path.isabs(arg)
        for _, arg in fs_spy
    )


@pytest.mark.parametrize("kind", ["dotdot", "relative", "nul"])
async def test_unsafe_retired_clip_paths_are_rejected_before_any_filesystem_call(
    factory, root, roots, monkeypatch, fs_spy, kind
):
    mp4 = _file(root / "Talk-abcdef12" / "Talk.mp4", age=timedelta(days=2))
    (root / "Other-11111111").mkdir()
    monkeypatch.chdir(root)
    await _add_clip(factory, await _add_job(factory, None), _unsafe_paths(root)[kind])
    fs_spy.clear()

    assert await _sweep_files(factory, roots) == []

    assert mp4.exists()
    assert [c for c in fs_spy if c[0] in ("unlink", "rmdir")] == []
    assert not any(
        "\x00" in arg or ".." in Path(arg).parts or not os.path.isabs(arg)
        for _, arg in fs_spy
    )


async def test_an_unsafe_live_path_still_protects_its_file(factory, root, roots):
    """A live clip's path spelled with ``..`` is not trusted for deleting,
    but it still marks its file as in use (lexically normalised)."""
    mp4 = _file(root / "c" / "00.mp4", age=timedelta(days=2))
    job_id = await _add_job(factory, None)
    await _add_clip(factory, job_id, str(mp4))
    await _add_clip(factory, job_id, f"{root}/x/../c/00.mp4", retired=False)

    assert await _sweep_files(factory, roots) == []

    assert mp4.exists()
