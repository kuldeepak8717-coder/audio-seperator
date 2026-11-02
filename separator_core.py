"""Shared audio-separation engine used by app.py and app2.py.

Features
--------
* Every pretrained Demucs v4 model (4-stem and 6-stem).
* WAV 16-bit, WAV 24-bit, FLAC, MP3 320 kbps and MP3 192 kbps output.
* Optional derived stems: instrumental (no vocals) and full mix.
* Batch packaging into one ZIP.
* CPU / CUDA device selection and custom segment size.
* Writes only to a writable runtime folder.  On Windows machines where
  Controlled Folder Access blocks Python inside Documents, it automatically
  falls back to ``%LOCALAPPDATA%\\audio-separator`` (then temp).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

APP_DIR = Path(__file__).resolve().parent

# Ensure bundled bin directory (ffmpeg, ffprobe) is in PATH
_BUNDLED_BIN = APP_DIR / "bin"
if _BUNDLED_BIN.is_dir():
    _bin_str = str(_BUNDLED_BIN)
    if _bin_str not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _bin_str + os.pathsep + os.environ.get("PATH", "")

# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------

FORMAT_WAV = "WAV 16-bit (Lossless)"
FORMAT_WAV24 = "WAV 24-bit (Studio)"
FORMAT_FLAC = "FLAC (Lossless, smaller)"
FORMAT_MP3 = "MP3 320 kbps (Best)"
FORMAT_MP3_192 = "MP3 192 kbps (Smaller)"

FORMAT_CHOICES = [
    FORMAT_WAV,
    FORMAT_WAV24,
    FORMAT_FLAC,
    FORMAT_MP3,
    FORMAT_MP3_192,
]

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

# name -> (label shown in the UI, stem count)
MODEL_INFO: dict[str, tuple[str, int]] = {
    "htdemucs_6s": (
        "HTDemucs 6s — 6 stems (Vocals, Drums, Bass, Piano, Guitar, Other)",
        6,
    ),
    "htdemucs_ft": (
        "HTDemucs FT — 4 stems, best quality (about 4x slower)",
        4,
    ),
    "htdemucs": ("HTDemucs — 4 stems, balanced", 4),
    "mdx_extra": ("MDX Extra — 4 stems, strong vocals", 4),
    "mdx_extra_q": ("MDX Extra Q — 4 stems, quantized/faster", 4),
    "mdx": ("MDX — 4 stems", 4),
    "mdx_q": ("MDX Q — 4 stems, quantized/faster", 4),
    "hdemucs_mmi": ("HDemucs MMI — 4 stems, legacy", 4),
    "repro_mdx_a": ("Repro MDX A — 4 stems, experimental", 4),
    "repro_mdx_a_hybrid_only": (
        "Repro MDX A Hybrid — 4 stems, experimental",
        4,
    ),
    "repro_mdx_a_time_only": ("Repro MDX A Time — 4 stems, experimental", 4),
}

MODEL_STEMS: dict[str, tuple[str, ...]] = {
    name: (
        ("vocals", "drums", "bass", "piano", "guitar", "other")
        if name == "htdemucs_6s"
        else ("vocals", "drums", "bass", "other")
    )
    for name in MODEL_INFO
}

DERIVED_STEMS = ("instrumental", "full_mix")

ProgressCallback = Callable[[float, str], None]


def model_choices() -> list[tuple[str, str]]:
    """Return (label, value) pairs for a Gradio Dropdown."""
    return [(label, name) for name, (label, _count) in MODEL_INFO.items()]


def available_device_choices() -> list[str]:
    """Only offer CUDA when this PyTorch build can actually see a GPU."""
    choices = ["Auto", "CPU"]
    try:
        import torch  # imported lazily; Demucs needs it anyway

        if torch.cuda.is_available():
            choices.append("GPU (CUDA)")
    except Exception:
        pass
    return choices


def resolve_device(device: str | None) -> str | None:
    if not device or device == "Auto":
        return None
    if str(device).startswith("GPU") or str(device).lower() == "cuda":
        try:
            import torch
        except ImportError as exc:
            raise SeparationError(
                "PyTorch इंस्टॉल नहीं है / PyTorch is not installed."
            ) from exc
        if not torch.cuda.is_available():
            raise SeparationError(
                "CUDA GPU नहीं मिला / No CUDA GPU detected.\n"
                "Device में 'Auto' या 'CPU' चुनें."
            )
        return "cuda"
    return "cpu"


# ---------------------------------------------------------------------------
# Errors / runtime folder
# ---------------------------------------------------------------------------


class SeparationError(RuntimeError):
    """An error that is safe and useful to show directly in the UI."""


def _configure_console_encoding() -> None:
    """Never crash just because the Windows console uses cp1252."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_configure_console_encoding()


def _noop_progress(_fraction: float, _description: str) -> None:
    pass


def _safe_name(value: object) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", str(value)).strip(" ._")


def _probe_writable(directory: Path) -> bool:
    """Return True only if we can really create *and* write inside a folder."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".write-test-", delete=True
        ):
            pass
        return True
    except OSError:
        return False


def _pick_runtime_dir() -> Path:
    """Pick the first writable location for generated files."""
    candidates: list[Path] = []

    env_home = os.environ.get("AUDIO_SEPARATOR_HOME")
    if env_home:
        candidates.append(Path(env_home).expanduser())

    # Preferred: keep outputs next to the project.
    candidates.append(APP_DIR / "runtime")

    # Fallback 1: per-user app data (works when Documents is locked down and
    # survives reboots).
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "audio-separator")

    # Fallback 2: system temp.
    candidates.append(Path(tempfile.gettempdir()) / "audio-separator")

    for candidate in candidates:
        if _probe_writable(candidate):
            return candidate.resolve()

    # Absolute last resort.
    return Path(tempfile.mkdtemp(prefix="audio-separator-")).resolve()


RUNTIME_DIR = _pick_runtime_dir()
SEPARATED_ROOT = RUNTIME_DIR / "separated"
DOWNLOADS_ROOT = RUNTIME_DIR / "downloads"

for _folder in (SEPARATED_ROOT, DOWNLOADS_ROOT):
    _folder.mkdir(parents=True, exist_ok=True)


def ensure_ffmpeg() -> None:
    """FFmpeg is required by Demucs, yt-dlp and the derived-stem mixer."""
    if shutil.which("ffmpeg") is None:
        raise SeparationError(
            "FFmpeg नहीं मिला / FFmpeg was not found on PATH.\n"
            "Windows: PowerShell में `winget install Gyan.FFmpeg` चलाएँ, "
            "फिर terminal / VS Code restart करें."
        )


def cleanup_old_files(max_age_hours: float = 24.0) -> None:
    """Delete generated files older than *max_age_hours* to save disk space."""
    cutoff = time.time() - max_age_hours * 3600
    for root in (SEPARATED_ROOT, DOWNLOADS_ROOT):
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.stat().st_mtime >= cutoff:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------


def download_youtube_audio(
    url: str, on_progress: ProgressCallback | None = None
) -> Path:
    """Download the audio of a YouTube URL and return the local WAV path."""
    notify = on_progress or _noop_progress
    url = (url or "").strip()
    if not url:
        raise SeparationError("YouTube link खाली है / YouTube URL is empty.")

    ensure_ffmpeg()
    DOWNLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    notify(0.04, "YouTube से ऑडियो डाउनलोड हो रहा है...")

    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise SeparationError(
            "yt-dlp इंस्टॉल नहीं है. `pip install -r requirements.txt` चलाएँ."
        ) from exc

    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": str(DOWNLOADS_ROOT / "%(title).120s [%(id)s].%(ext)s"),
        "windowsfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "wav"}
        ],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise SeparationError("YouTube video की जानकारी नहीं मिली.")

            # If a playlist URL was pasted, only use the first playable video.
            if info.get("_type") == "playlist" or info.get("entries"):
                entries = [entry for entry in (info.get("entries") or []) if entry]
                if not entries:
                    raise SeparationError(
                        "इस playlist में कोई playable video नहीं मिला."
                    )
                info = entries[0]

            # yt-dlp updates requested_downloads with the final post-processed path.
            requested = info.get("requested_downloads") or []
            candidate = requested[0].get("filepath") if requested else None
            if not candidate:
                candidate = ydl.prepare_filename(info)
    except SeparationError:
        raise
    except Exception as exc:  # yt-dlp raises many different exception types
        raise SeparationError(f"YouTube download failed: {exc}") from exc

    path = Path(candidate)
    if not path.is_file():
        wav_path = path.with_suffix(".wav")
        if wav_path.is_file():
            path = wav_path

    # Last resort: find the newest file that contains the video id.
    if not path.is_file() and info.get("id"):
        video_id = str(info["id"])
        matches = [
            item
            for item in DOWNLOADS_ROOT.iterdir()
            if item.is_file() and video_id in item.name
        ]
        if matches:
            path = max(matches, key=lambda item: item.stat().st_mtime)

    if not path.is_file():
        raise SeparationError(
            "डाउनलोड हुई ऑडियो फाइल नहीं मिली. FFmpeg इंस्टॉल है या नहीं जाँचें."
        )

    notify(0.12, "ऑडियो डाउनलोड पूरा हुआ.")
    return path.resolve()


# ---------------------------------------------------------------------------
# Separation
# ---------------------------------------------------------------------------


@dataclass
class SeparationResult:
    track_name: str
    model_name: str = ""
    output_format: str = ""
    stems: dict[str, str | None] = field(default_factory=dict)
    zip_path: str | None = None
    run_dir: str = ""

    @property
    def stem_count(self) -> int:
        return sum(1 for path in self.stems.values() if path)


def _format_spec(output_format: str) -> tuple[str, list[str], list[str]]:
    """Return (extension, demucs arguments, ffmpeg codec arguments)."""
    if output_format == FORMAT_WAV24:
        return ".wav", ["--int24"], ["-c:a", "pcm_s24le"]
    if output_format == FORMAT_FLAC:
        return ".flac", ["--flac"], ["-c:a", "flac"]
    if output_format == FORMAT_MP3:
        return (
            ".mp3",
            ["--mp3", "--mp3-bitrate", "320"],
            ["-c:a", "libmp3lame", "-b:a", "320k"],
        )
    if output_format == FORMAT_MP3_192:
        return (
            ".mp3",
            ["--mp3", "--mp3-bitrate", "192"],
            ["-c:a", "libmp3lame", "-b:a", "192k"],
        )
    return ".wav", [], ["-c:a", "pcm_s16le"]


def _mix_audio(
    input_paths: Iterable[str | None],
    output_path: Path,
    codec_args: list[str],
) -> str | None:
    """Mix several stems into one file with FFmpeg. Returns None on failure."""
    paths = [str(path) for path in input_paths if path and Path(path).is_file()]
    if not paths:
        return None

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if len(paths) == 1:
        cmd += ["-i", paths[0], *codec_args]
    else:
        for path in paths:
            cmd += ["-i", path]
        filter_graph = (
            f"amix=inputs={len(paths)}:duration=longest:normalize=0,"
            "alimiter=limit=0.98"
        )
        cmd += ["-filter_complex", filter_graph, *codec_args]
    cmd.append(str(output_path))

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except OSError as exc:
        print(f"[audio-separator] ffmpeg mix failed: {exc}")
        return None

    if completed.returncode != 0 or not output_path.is_file():
        tail = (completed.stderr or completed.stdout or "").strip()[-600:]
        print(f"[audio-separator] ffmpeg mix failed: {tail}")
        return None

    return str(output_path)


def _create_derived_stems(
    track_dir: Path,
    stems: dict[str, str | None],
    extension: str,
    codec_args: list[str],
) -> dict[str, str]:
    """Create instrumental (vocals removed) and full mix (all stems)."""
    available = {name: path for name, path in stems.items() if path}
    derived: dict[str, str | None] = {}

    non_vocal = [
        path for name, path in available.items() if name not in {"vocals"}
    ]
    if non_vocal:
        derived["instrumental"] = _mix_audio(
            non_vocal, track_dir / f"instrumental{extension}", codec_args
        )

    if len(available) >= 2:
        derived["full_mix"] = _mix_audio(
            list(available.values()), track_dir / f"full_mix{extension}", codec_args
        )

    return {name: path for name, path in derived.items() if path}


def separate(
    audio_path: str | os.PathLike[str],
    model_name: str = "htdemucs_6s",
    shifts: int = 2,
    overlap: float = 0.25,
    output_format: str = FORMAT_WAV,
    make_mixes: bool = True,
    device: str | None = None,
    segment: float | int | None = None,
    on_progress: ProgressCallback | None = None,
) -> SeparationResult:
    """Run Demucs on *audio_path* and return all generated stems + a ZIP."""
    notify = on_progress or _noop_progress

    if model_name not in MODEL_STEMS:
        raise SeparationError(f"अज्ञात मॉडल / Unknown model: {model_name}")
    if not audio_path:
        raise SeparationError("कोई ऑडियो फाइल नहीं मिली / No audio file provided.")

    source = Path(audio_path).expanduser().resolve()
    if not source.is_file():
        raise SeparationError(f"ऑडियो फाइल नहीं मिली / Audio file not found:\n{source}")

    ensure_ffmpeg()
    cleanup_old_files()

    try:
        shifts = max(1, min(int(shifts), 20))
    except (TypeError, ValueError):
        shifts = 2

    try:
        overlap = max(0.0, min(float(overlap), 1.0))
    except (TypeError, ValueError):
        overlap = 0.25

    try:
        segment_value = int(max(0.0, min(float(segment or 0), 120.0)))
    except (TypeError, ValueError):
        segment_value = 0

    resolved_device = resolve_device(device)
    extension, demucs_format_args, codec_args = _format_spec(output_format)

    SEPARATED_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=str(SEPARATED_ROOT)))

    def build_cmd(segment_seconds: float) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "demucs",
            "-n",
            model_name,
            "--shifts",
            str(shifts),
            "--overlap",
            f"{overlap:g}",
            *demucs_format_args,
        ]
        if resolved_device:
            command += ["-d", resolved_device]
        if segment_seconds > 0:
            command += ["--segment", str(int(segment_seconds))]
        command += ["-o", str(run_dir), str(source)]
        return command

    cmd = build_cmd(segment_value)

    notify(
        0.10,
        f"Demucs ({model_name}) स्टेम्स अलग कर रहा है... थोड़ा समय लग सकता है.",
    )

    env = os.environ.copy()
    env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    attempts = 0
    while True:
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except OSError as exc:
            raise SeparationError(
                f"Demucs शुरू नहीं हो सका / Could not start Demucs: {exc}"
            ) from exc

        if completed.returncode == 0:
            break

        output = ((completed.stderr or "") + "\n" + (completed.stdout or "")).strip()

        # Some Demucs models have a hard maximum segment length (e.g. 7.8 s).
        # Clamp automatically instead of failing the whole job.
        match = re.search(r"Maximum segment is:\s*([0-9.]+)", output)
        if match and segment_value > 0 and attempts == 0:
            max_segment = float(match.group(1))
            clamped = max(1, int(max_segment)) if max_segment >= 1 else 0
            if clamped < segment_value:
                print(
                    "[audio-separator] Segment "
                    f"{segment_value:g}s is too large; retrying with "
                    f"{clamped if clamped else 'auto'}s"
                )
                segment_value = clamped
                cmd = build_cmd(segment_value)
                attempts += 1
                continue

        tail = "\n".join(output.splitlines()[-12:]) or "No output captured."
        raise SeparationError(
            "Demucs separation failed.\n"
            "पहली बार चलाने पर model download के लिए internet चाहिए.\n\n"
            f"{tail}"
        )

    notify(0.82, "स्टेम फाइलें तैयार हो रही हैं...")

    # Demucs writes to <out>/<model>/<track>/.  Be tolerant about the exact
    # track folder name because Gradio may give the file a random temp name.
    model_root = run_dir / model_name
    expected_dir = model_root / source.stem
    if expected_dir.is_dir():
        track_dir = expected_dir
    elif model_root.is_dir():
        subdirs = sorted(item for item in model_root.iterdir() if item.is_dir())
        if not subdirs:
            raise SeparationError("Demucs ने कोई output फोल्डर नहीं बनाया.")
        track_dir = subdirs[0]
    else:
        raise SeparationError("Demucs output फोल्डर नहीं मिला.")

    stems: dict[str, str | None] = {}
    for stem in MODEL_STEMS[model_name]:
        candidate = track_dir / f"{stem}{extension}"
        if candidate.is_file():
            stems[stem] = str(candidate)
        else:
            stems[stem] = None
            print(f"[audio-separator] Missing stem: {candidate}")

    available = {name: path for name, path in stems.items() if path}
    if not available:
        raise SeparationError(
            "कोई भी stem file generate नहीं हुई. ऊपर Demucs का log देखें."
        )

    if make_mixes:
        notify(0.88, "Instrumental और Full Mix बन रहे हैं...")
        stems.update(_create_derived_stems(track_dir, stems, extension, codec_args))

    notify(0.94, "ZIP पैकेज बन रहा है...")

    safe_track = _safe_name(track_dir.name) or "stems"
    zip_path = run_dir / f"{safe_track}_stems.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in stems.values():
            if path and Path(path).is_file():
                archive.write(path, arcname=os.path.basename(path))

    notify(1.0, "पूरा हुआ!")

    return SeparationResult(
        track_name=track_dir.name,
        model_name=model_name,
        output_format=output_format,
        stems=stems,
        zip_path=str(zip_path) if zip_path.is_file() else None,
        run_dir=str(run_dir),
    )


def package_results(
    results: list[SeparationResult], name: str = "audio_separator_batch"
) -> str | None:
    """Bundle several results into one ZIP, one folder per track."""
    if not results:
        return None

    SEPARATED_ROOT.mkdir(parents=True, exist_ok=True)
    batch_dir = Path(tempfile.mkdtemp(prefix="batch-", dir=str(SEPARATED_ROOT)))
    zip_path = batch_dir / f"{_safe_name(name) or 'batch'}.zip"

    used: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for result in results:
            folder = _safe_name(result.track_name) or "track"
            candidate = folder
            counter = 2
            while candidate.lower() in used:
                candidate = f"{folder} ({counter})"
                counter += 1
            used.add(candidate.lower())

            for path in result.stems.values():
                if path and Path(path).is_file():
                    archive.write(path, arcname=f"{candidate}/{Path(path).name}")

    return str(zip_path) if zip_path.is_file() else None

