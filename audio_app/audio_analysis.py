"""
All ffmpeg/ffprobe interaction for the audio collection app.

The original upload is probed, converted to a canonical PCM WAV, and analyzed
for loudness and a rough speech-quality estimate.
"""

import json
import re
import shutil
import subprocess
import wave
from pathlib import Path


class AudioAnalysisError(Exception):
    """Raised when an upload cannot be decoded or analyzed."""


def require_tools():
    """Return the ffmpeg tools, or fail with an actionable error."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not (ffmpeg and ffprobe):
        raise AudioAnalysisError(
            "ffmpeg/ffprobe not found on PATH — install ffmpeg first"
        )
    return ffmpeg, ffprobe


def _run(cmd, timeout=90):
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioAnalysisError("audio processing timed out") from exc
    except OSError as exc:
        raise AudioAnalysisError(f"could not run audio tool: {exc}") from exc


def probe(path, ffprobe=None):
    """Return metadata from the original file. Any value can be None."""
    if ffprobe is None:
        _, ffprobe = require_tools()
    process = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    if process.returncode != 0:
        raise AudioAnalysisError(
            "not decodable audio: " + process.stderr.strip()[:200]
        )

    try:
        info = json.loads(process.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AudioAnalysisError("ffprobe returned invalid metadata") from exc

    audio = [
        stream
        for stream in info.get("streams", [])
        if stream.get("codec_type") == "audio"
    ]
    if not audio:
        raise AudioAnalysisError("file contains no audio stream")
    stream, file_format = audio[0], info.get("format", {})

    def num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return {
        "duration_sec": num(file_format.get("duration"))
        or num(stream.get("duration")),
        "sample_rate_hz": num(stream.get("sample_rate")),
        "bitrate_bps": num(file_format.get("bit_rate"))
        or num(stream.get("bit_rate")),
        "codec": stream.get("codec_name"),
    }


def to_wav(src, dst, ffmpeg=None):
    """Create a canonical 16-bit PCM WAV while preserving rate and channels."""
    if ffmpeg is None:
        ffmpeg, _ = require_tools()
    process = _run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-acodec",
            "pcm_s16le",
            str(dst),
        ]
    )
    if process.returncode != 0 or not Path(dst).exists():
        raise AudioAnalysisError(
            "wav conversion failed: " + process.stderr.strip()[:200]
        )


def wav_facts(path):
    """Read exact duration and PCM properties from the canonical WAV."""
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
    except (wave.Error, OSError) as exc:
        raise AudioAnalysisError("converted WAV is invalid") from exc

    return {
        "duration_sec": frames / sample_rate if sample_rate else None,
        "sample_rate_hz": float(sample_rate),
        "pcm_bitrate_bps": float(
            sample_rate * channels * sample_width * 8
        ),
    }


def loudness_and_noise(wav_path, ffmpeg=None):
    """Extract dBFS loudness and a rough pause-based speech SNR estimate."""
    if ffmpeg is None:
        ffmpeg, _ = require_tools()
    # Do not use "-v error": both filters report measurements at info level.
    process = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(wav_path),
            "-af",
            "volumedetect,astats",
            "-f",
            "null",
            "-",
        ]
    )
    if process.returncode != 0:
        raise AudioAnalysisError(
            "audio analysis failed: " + process.stderr.strip()[:200]
        )
    text = process.stderr

    def grab_last(pattern):
        hits = re.findall(pattern, text)
        if not hits:
            return None
        value = float(hits[-1])
        return max(value, -99.0)

    number = r"(-?(?:[\d.]+|inf))"
    mean_db = grab_last(r"mean_volume:\s*" + number)
    max_db = grab_last(r"max_volume:\s*" + number)
    # astats emits per-channel blocks followed by Overall; use the last value.
    # FFmpeg 9 renamed "RMS trough dB" -> "RMS through dB" and also prints
    # "Noise floor dB"; accept all three so older and newer builds work.
    rms_db = grab_last(r"RMS level dB:\s*" + number)
    floor_db = (
        grab_last(r"RMS trough dB:\s*" + number)
        or grab_last(r"RMS through dB:\s*" + number)
        or grab_last(r"Noise floor dB:\s*" + number)
    )

    snr = (
        round(rms_db - floor_db, 1)
        if rms_db is not None and floor_db is not None
        else None
    )
    clipping = int(max_db is not None and max_db > -1.0)
    if snr is None:
        label = "unknown"
    elif snr >= 30:
        label = "good"
    elif snr >= 15:
        label = "fair"
    else:
        label = "noisy"

    return {
        "loudness_db": mean_db,
        "peak_db": max_db,
        "noise_floor_db": floor_db,
        "snr_db": snr,
        "quality_label": label,
        "clipping": clipping,
    }


def analyze(original_path, wav_path):
    """Run the full extraction and return values matching the DB columns."""
    ffmpeg, ffprobe = require_tools()
    metadata = probe(original_path, ffprobe)
    to_wav(original_path, wav_path, ffmpeg)
    wav_metadata = wav_facts(wav_path)
    loudness = loudness_and_noise(wav_path, ffmpeg)

    duration = metadata["duration_sec"] or wav_metadata["duration_sec"]
    sample_rate = metadata["sample_rate_hz"] or wav_metadata["sample_rate_hz"]
    bitrate = metadata["bitrate_bps"] or wav_metadata["pcm_bitrate_bps"]

    return {
        "duration_sec": round(duration, 2) if duration else None,
        "sample_rate_khz": round(sample_rate / 1000, 2)
        if sample_rate
        else None,
        "bitrate_kbps": round(bitrate / 1000, 1) if bitrate else None,
        "codec": metadata["codec"],
        **loudness,
    }
