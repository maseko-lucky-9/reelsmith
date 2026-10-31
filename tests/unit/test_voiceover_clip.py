"""Clip voice-over synthesis (spec 016): ``voiceover_service.synthesize_voiceover``,
``resolve_provider``, text and voice validation, the wav checks, the settings
defaults and the voice download script's URLs. Offline: Piper is never run
(a fake runner stands in for it) and nothing is downloaded."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.services import voiceover_service as svc
from app.settings import Settings, settings
from scripts import fetch_piper_voice

VOICE = "en_US-lessac-medium"


def _wav(path: Path, seconds: float = 0.5, rate: int = 22050) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


@pytest.fixture
def voices(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "models"
    folder.mkdir()
    monkeypatch.setattr(settings, "voiceover_model_dir", str(folder))
    monkeypatch.setattr(settings, "voiceover_voice", VOICE)
    return folder


def _install_voice(folder: Path, voice: str = VOICE) -> None:
    (folder / f"{voice}.onnx").write_bytes(b"model")
    (folder / f"{voice}.onnx.json").write_text("{}")


# ── Settings ─────────────────────────────────────────────────────────────────


def test_settings_defaults(monkeypatch):
    for name in ("VOICEOVER_PROVIDER", "VOICEOVER_MODEL_DIR", "VOICEOVER_VOICE"):
        monkeypatch.delenv(f"YTVIDEO_{name}", raising=False)
    fresh = Settings(_env_file=None)
    assert fresh.voiceover_provider == "auto"
    assert fresh.voiceover_voice == "en_US-lessac-medium"
    assert Path(fresh.voiceover_model_dir).parts[-2:] == ("data", "models")


# ── Provider selection ───────────────────────────────────────────────────────


def test_auto_is_stub_without_piper(voices, monkeypatch):
    _install_voice(voices)
    monkeypatch.setattr(svc, "piper_command", lambda: None)
    assert svc.resolve_provider("auto") == "stub"


def test_auto_is_stub_without_the_voice(voices, monkeypatch, caplog):
    monkeypatch.setattr(svc, "piper_command", lambda: ["/bin/piper"])
    with caplog.at_level("INFO"):
        assert svc.resolve_provider("auto") == "stub"
    assert "fetch_piper_voice" in caplog.text


def test_auto_is_stub_with_the_model_but_no_config(voices, monkeypatch):
    (voices / f"{VOICE}.onnx").write_bytes(b"model")
    monkeypatch.setattr(svc, "piper_command", lambda: ["/bin/piper"])
    assert svc.resolve_provider("auto") == "stub"


def test_auto_is_piper_with_piper_and_the_voice(voices, monkeypatch):
    _install_voice(voices)
    monkeypatch.setattr(svc, "piper_command", lambda: ["/bin/piper"])
    assert svc.resolve_provider("auto") == "piper"


def test_default_provider_comes_from_settings(voices, monkeypatch):
    monkeypatch.setattr(settings, "voiceover_provider", "stub")
    monkeypatch.setattr(svc, "piper_command", lambda: ["/bin/piper"])
    _install_voice(voices)
    assert svc.resolve_provider() == "stub"


def test_explicit_providers_and_off(voices):
    assert svc.resolve_provider("piper") == "piper"
    assert svc.resolve_provider("STUB") == "stub"
    with pytest.raises(svc.VoiceoverDisabled):
        svc.resolve_provider("off")
    with pytest.raises(svc.VoiceoverError, match="unknown"):
        svc.resolve_provider("coqui-ish")


# ── Text and voice validation ────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["", "   ", "\x00\x07\n\t", 12])
def test_empty_or_non_text_is_refused(text):
    with pytest.raises(svc.VoiceoverInputError):
        svc.clean_text(text)


def test_over_long_text_is_refused():
    svc.clean_text("a" * svc.MAX_TEXT_CHARS)
    with pytest.raises(svc.VoiceoverInputError, match="longer"):
        svc.clean_text("a" * (svc.MAX_TEXT_CHARS + 1))


def test_control_and_format_characters_are_stripped():
    raw = "Hello\x00 world\n\r\x1b[31m now‮​!"
    assert svc.clean_text(raw) == "Hello world [31m now !"


@pytest.mark.parametrize(
    "voice", ["../evil", "a/b", "..", ".", "/etc/passwd", "x" * 65, "", "voice\x00", "a b", "..\\x", None]
)
def test_bad_voice_names_are_refused(voice, voices):
    with pytest.raises(svc.VoiceoverInputError):
        svc.voice_model_path(voice)


def test_voice_resolves_inside_the_voice_folder(voices):
    assert svc.voice_model_path(VOICE) == voices.resolve() / f"{VOICE}.onnx"


def test_a_symlinked_voice_outside_the_folder_is_refused(voices, tmp_path):
    outside = tmp_path / "elsewhere.onnx"
    outside.write_bytes(b"x")
    (voices / "linked.onnx").symlink_to(outside)
    with pytest.raises(svc.VoiceoverInputError, match="outside"):
        svc.voice_model_path("linked")


# ── Synthesis ────────────────────────────────────────────────────────────────


def test_stub_writes_a_one_second_wav(voices, tmp_path):
    out = tmp_path / "vo.wav"
    assert svc.synthesize_voiceover("hello", None, out, provider="stub") == pytest.approx(1.0)
    with wave.open(str(out)) as wav:
        assert wav.getnframes() > 0


def test_stub_still_validates_text_and_voice(voices, tmp_path):
    with pytest.raises(svc.VoiceoverInputError):
        svc.synthesize_voiceover("  ", None, tmp_path / "a.wav", provider="stub")
    with pytest.raises(svc.VoiceoverInputError):
        svc.synthesize_voiceover("hi", "../x", tmp_path / "b.wav", provider="stub")
    assert not (tmp_path / "a.wav").exists() and not (tmp_path / "b.wav").exists()


def test_piper_gets_an_argv_list_and_the_text_through_a_file(voices, tmp_path, monkeypatch):
    _install_voice(voices)
    monkeypatch.setattr(svc, "piper_command", lambda: ["/opt/piper"])
    out = tmp_path / "vo.wav"
    seen: dict = {}

    def runner(argv, timeout):
        seen["argv"], seen["timeout"] = list(argv), timeout
        text_file = Path(argv[argv.index("--input-file") + 1])
        seen["text"] = text_file.read_text(encoding="utf-8")
        _wav(Path(argv[argv.index("--output-file") + 1]), seconds=0.75)

    text = "-rf $(rm -rf /); `id` --model x\x07"
    seconds = svc.synthesize_voiceover(text, VOICE, out, provider="piper", runner=runner)

    assert seconds == pytest.approx(0.75, abs=1e-3)
    argv = seen["argv"]
    assert argv[0] == "/opt/piper"
    assert argv[argv.index("--model") + 1] == str(voices.resolve() / f"{VOICE}.onnx")
    assert argv[argv.index("--config") + 1] == str(voices.resolve() / f"{VOICE}.onnx.json")
    assert argv[argv.index("--output-file") + 1] == str(out)
    assert all("rm -rf" not in a for a in argv)
    assert seen["text"] == "-rf $(rm -rf /); `id` --model x\n"
    assert seen["timeout"] == svc.SYNTH_TIMEOUT_SECONDS
    # The temporary text file is gone.
    assert not Path(argv[argv.index("--input-file") + 1]).exists()


def test_piper_without_the_voice_fails_before_running(voices, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "piper_command", lambda: ["/opt/piper"])
    calls = []
    with pytest.raises(svc.VoiceoverError, match="fetch_piper_voice"):
        svc.synthesize_voiceover(
            "hi", VOICE, tmp_path / "vo.wav", provider="piper", runner=lambda a, t: calls.append(a)
        )
    assert calls == []


def test_piper_not_installed_fails(voices, tmp_path, monkeypatch):
    _install_voice(voices)
    monkeypatch.setattr(svc, "piper_command", lambda: None)
    with pytest.raises(svc.VoiceoverError, match="not installed"):
        svc.synthesize_voiceover("hi", VOICE, tmp_path / "vo.wav", provider="piper", runner=lambda a, t: None)


def test_runner_failures_become_voiceover_errors(voices, tmp_path, monkeypatch):
    from app.services import ffmpeg_tools

    def boom(argv, *, timeout=None, **_):
        raise ffmpeg_tools.FfmpegTimeout("piper timed out", returncode=None, stderr_tail="")

    monkeypatch.setattr(ffmpeg_tools, "run", boom)
    _install_voice(voices)
    monkeypatch.setattr(svc, "piper_command", lambda: ["/opt/piper"])
    with pytest.raises(svc.VoiceoverError, match="piper failed"):
        svc.synthesize_voiceover("hi", VOICE, tmp_path / "vo.wav", provider="piper")


def test_cancellation_is_not_swallowed(voices, tmp_path, monkeypatch):
    from app.services import ffmpeg_tools

    def cancelled(argv, *, timeout=None, **_):
        raise ffmpeg_tools.FfmpegCancelled("cancelled", returncode=None, stderr_tail="")

    monkeypatch.setattr(ffmpeg_tools, "run", cancelled)
    _install_voice(voices)
    monkeypatch.setattr(svc, "piper_command", lambda: ["/opt/piper"])
    with pytest.raises(ffmpeg_tools.FfmpegCancelled):
        svc.synthesize_voiceover("hi", VOICE, tmp_path / "vo.wav", provider="piper")


@pytest.mark.parametrize("content", [b"", b"not a wav at all, just bytes"])
def test_bad_piper_output_is_refused(voices, tmp_path, monkeypatch, content):
    _install_voice(voices)
    monkeypatch.setattr(svc, "piper_command", lambda: ["/opt/piper"])
    out = tmp_path / "vo.wav"
    with pytest.raises(svc.VoiceoverError):
        svc.synthesize_voiceover("hi", VOICE, out, provider="piper", runner=lambda a, t: out.write_bytes(content))


def test_wav_checks(tmp_path, monkeypatch):
    with pytest.raises(svc.VoiceoverError, match="no file"):
        svc.wav_duration(tmp_path / "missing.wav")
    empty_audio = _wav(tmp_path / "zero.wav", seconds=0)
    with pytest.raises(svc.VoiceoverError, match="no audio"):
        svc.wav_duration(empty_audio)
    big = _wav(tmp_path / "big.wav", seconds=1)
    monkeypatch.setattr(svc, "MAX_WAV_BYTES", 100)
    with pytest.raises(svc.VoiceoverError, match="larger"):
        svc.wav_duration(big)


def test_reserved_names_are_unique_and_inside_the_folder(tmp_path):
    first = svc.reserve_output_path(tmp_path)
    second = svc.reserve_output_path(tmp_path)
    assert (first.name, second.name) == ("voiceover_1.wav", "voiceover_2.wav")
    assert first.parent == second.parent == tmp_path
    first.unlink()
    assert svc.reserve_output_path(tmp_path).name == "voiceover_1.wav"


def test_legacy_synthesize_is_unchanged(tmp_path):
    out = tmp_path / "vo.wav"
    assert svc.synthesize("hello", str(out), provider="stub") == str(out)


# ── Voice download script (no network) ───────────────────────────────────────


def test_fetch_script_urls_point_at_the_official_voice_repo():
    urls = fetch_piper_voice.voice_urls("en_US-lessac-medium")
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/"
    assert urls == {
        "en_US-lessac-medium.onnx": base + "en_US-lessac-medium.onnx?download=true",
        "en_US-lessac-medium.onnx.json": base + "en_US-lessac-medium.onnx.json?download=true",
    }


@pytest.mark.parametrize("voice", ["../x", "en_US-lessac", "en_US-a/b-medium", "en_US-lessac-medium?x=1"])
def test_fetch_script_refuses_bad_voice_names(voice):
    with pytest.raises(ValueError):
        fetch_piper_voice.voice_urls(voice)


def test_fetch_script_keeps_existing_files_without_network(tmp_path, monkeypatch):
    _install_voice(tmp_path)

    def no_network(*a, **k):
        raise AssertionError("network used")

    monkeypatch.setattr(fetch_piper_voice.urllib.request, "urlopen", no_network)
    assert fetch_piper_voice.main(["--dest", str(tmp_path)]) == 0
