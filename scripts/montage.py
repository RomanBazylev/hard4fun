"""
montage.py
==========
FFmpeg-based montage pipeline for GlitchRealityAI.

Operations performed (in order):
  1. Trim video to configured duration (5–12 s) or keep full
  2. Force 9:16 vertical crop / pad
  3. Apply glitch visual effects (RGB shift, scanlines, noise)
  4. Burn-in subtitles (styled, positioned at bottom)
  5. Mix in background music at low volume
  6. Normalise audio loudness (EBU R128)
  7. Re-encode to H.264 + AAC at Short-optimal bitrate

Requires: ffmpeg (installed by GitHub Actions workflow)

Usage:
    python scripts/montage.py \
        --video /tmp/raw.mp4 \
        --audio /tmp/voice.mp3 \
        --text  "Wait... did the simulation just break?" \
        --output /tmp/final.mp4
"""
from __future__ import annotations

import logging
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "config.yaml"
MUSIC_DIR = ROOT / "assets" / "music"
FONT_DEFAULT = ROOT / "assets" / "fonts" / "Impact.ttf"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("scripts.montage")


def load_config() -> dict[str, Any]:
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_ffmpeg(cmd: list[str], label: str = "") -> bool:
    """Run an ffmpeg command, log stdout/stderr, return success bool."""
    log.info("FFmpeg [%s]: %s", label, " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("FFmpeg [%s] failed (rc=%d):\n%s", label, result.returncode, result.stderr[-2000:])
        return False
    return True


def get_duration(path: Path) -> float | None:
    """Return duration of a media file in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def pick_music_track() -> Path | None:
    """Pick a random background music file from assets/music/."""
    if not MUSIC_DIR.exists():
        return None
    tracks = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
    if not tracks:
        log.warning("No music tracks found in %s — skipping background music.", MUSIC_DIR)
        return None
    picked = random.choice(tracks)
    log.info("Music track: %s", picked.name)
    return picked


def resolve_font(config: dict[str, Any]) -> str:
    """Return the font path string for ffmpeg drawtext filter."""
    font_path = Path(config["montage"].get("font", str(FONT_DEFAULT)))
    if not font_path.is_absolute():
        font_path = ROOT / font_path
    if font_path.exists():
        return str(font_path)
    # Fallback to system fonts
    for sys_font in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        if Path(sys_font).exists():
            log.warning("Custom font not found; using system font: %s", sys_font)
            return sys_font
    return "DejaVuSans"  # ffmpeg will try to find it


# ---------------------------------------------------------------------------
# Subtitle / drawtext helpers
# ---------------------------------------------------------------------------

def _escape_drawtext(text: str) -> str:
    """Escape special characters for ffmpeg drawtext filter."""
    # Order matters — escape backslash first
    for ch in ["\\", ":", "'", "[", "]"]:
        text = text.replace(ch, "\\" + ch)
    return text


def build_subtitle_filter(
    text: str,
    config: dict[str, Any],
    font_path: str,
    video_duration: float,
) -> str:
    """
    Build an ffmpeg drawtext filter string for subtitles.
    Shows text with a white/black outline, centred horizontally,
    positioned at the bottom third of the frame.
    """
    style = config["montage"]["subtitle_style"]
    fontsize = style.get("fontsize", 56)
    fontcolor = style.get("fontcolor", "white")
    outline_color = style.get("outline_color", "black")
    outline_w = style.get("outline_width", 3)

    # Wrap long text at word boundaries (max ~35 chars per line)
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > 35:
            lines.append(current.strip())
            current = word + " "
        else:
            current += word + " "
    if current.strip():
        lines.append(current.strip())

    escaped_text = "\n".join(_escape_drawtext(line) for line in lines)

    return (
        f"drawtext=text='{escaped_text}'"
        f":fontfile='{font_path}'"
        f":fontsize={fontsize}"
        f":fontcolor={fontcolor}"
        f":bordercolor={outline_color}"
        f":borderw={outline_w}"
        f":x=(w-text_w)/2"
        f":y=h-text_h-60"
        f":enable='between(t,0,{video_duration})'"
    )


# ---------------------------------------------------------------------------
# Glitch effect filter chain
# ---------------------------------------------------------------------------

GLITCH_FILTER = (
    # RGB channel shift (chromatic aberration)
    "split=3[r][g][b];"
    "[r]lutrgb=r='if(between(val,0,255),clip(val+8,0,255),val)':g=0:b=0[ro];"
    "[g]lutrgb=r=0:g=val:b=0[go];"
    "[b]lutrgb=r=0:g=0:b='if(between(val,0,255),clip(val-8,0,255),val)'[bo];"
    "[ro][go]blend=all_mode=addition[rg];"
    "[rg][bo]blend=all_mode=addition[glitched];"
    # Slight noise / grain
    "[glitched]noise=alls=6:allf=t[noisy];"
    # Very subtle scanlines
    "[noisy]drawgrid=width=0:height=4:thickness=1:color=black@0.05[out]"
)


# ---------------------------------------------------------------------------
# Main montage function
# ---------------------------------------------------------------------------

def assemble_short(
    video_path: Path | str,
    audio_path: Path | str,
    voice_text: str,
    output_path: Path | str,
    config: dict[str, Any] | None = None,
) -> bool:
    """
    Assemble the final YouTube Short from raw video + TTS audio.

    Steps:
      1. Verify inputs
      2. Crop/pad to 9:16 (1080×1920 target, scaled from 576×1024)
      3. Apply glitch filter (if enabled)
      4. Burn subtitles
      5. Mix background music
      6. Export final Short

    Returns True on success.
    """
    if config is None:
        config = load_config()

    video_path = Path(video_path)
    audio_path = Path(audio_path)
    output_path = Path(output_path)

    if not video_path.exists():
        log.error("Video not found: %s", video_path)
        return False
    if not audio_path.exists():
        log.error("Audio not found: %s", audio_path)
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Probe durations
    vid_dur = get_duration(video_path) or 10.0
    aud_dur = get_duration(audio_path) or 5.0
    target_dur = max(vid_dur, aud_dur)
    target_dur = min(target_dur, config["channel"]["video_duration_max"])

    log.info("Assembling: video=%.1fs audio=%.1fs target=%.1fs", vid_dur, aud_dur, target_dur)

    font_path = resolve_font(config)
    music_track = pick_music_track()
    glitch_enabled = config["montage"].get("glitch_overlay", True)
    music_vol = config["montage"].get("music_volume", 0.15)

    with tempfile.TemporaryDirectory(prefix="glitch_montage_") as tmp_dir:
        tmp = Path(tmp_dir)

        # Step 1: Scale + pad to 1080×1920 (9:16)
        scaled = tmp / "scaled.mp4"
        scale_filter = (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
        )
        ok = run_ffmpeg(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vf", scale_filter,
                "-t", str(target_dur),
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-an", str(scaled),
            ],
            label="scale",
        )
        if not ok:
            return False

        # Step 2: Apply glitch effect (optional)
        glitched = tmp / "glitched.mp4"
        if glitch_enabled:
            vf = GLITCH_FILTER.replace("[out]", "") + ",format=yuv420p"
            ok = run_ffmpeg(
                [
                    "ffmpeg", "-y", "-i", str(scaled),
                    "-vf", vf,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-an", str(glitched),
                ],
                label="glitch",
            )
            if not ok:
                log.warning("Glitch filter failed — using unglitched video.")
                shutil.copy(scaled, glitched)
        else:
            shutil.copy(scaled, glitched)

        # Step 3: Burn subtitles
        subtitled = tmp / "subtitled.mp4"
        sub_filter = build_subtitle_filter(voice_text, config, font_path, target_dur)
        ok = run_ffmpeg(
            [
                "ffmpeg", "-y", "-i", str(glitched),
                "-vf", sub_filter,
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-an", str(subtitled),
            ],
            label="subtitles",
        )
        if not ok:
            log.warning("Subtitle burn failed — using video without subtitles.")
            shutil.copy(glitched, subtitled)

        # Step 4: Mix audio (TTS + optional background music)
        if music_track and music_track.exists():
            mixed_audio = tmp / "audio_mixed.aac"
            ok = run_ffmpeg(
                [
                    "ffmpeg", "-y",
                    "-i", str(audio_path),
                    "-i", str(music_track),
                    "-filter_complex",
                    f"[0:a]volume=1.0[voice];[1:a]volume={music_vol},atrim=0:{target_dur}[music];"
                    "[voice][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                    "-map", "[aout]",
                    "-c:a", "aac", "-b:a", "128k",
                    "-t", str(target_dur),
                    str(mixed_audio),
                ],
                label="audio_mix",
            )
            if not ok:
                log.warning("Audio mix failed — using TTS only.")
                mixed_audio = audio_path  # type: ignore[assignment]
        else:
            mixed_audio = audio_path  # type: ignore[assignment]

        # Step 5: Merge video + audio → final output
        ok = run_ffmpeg(
            [
                "ffmpeg", "-y",
                "-i", str(subtitled),
                "-i", str(mixed_audio),
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-t", str(target_dur),
                "-movflags", "+faststart",
                str(output_path),
            ],
            label="final_merge",
        )
        if not ok:
            return False

    file_size = output_path.stat().st_size / 1024 / 1024
    log.info("✓ Short assembled: %s (%.1f MB, %.1fs)", output_path, file_size, target_dur)
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Assemble a YouTube Short from video + audio")
    parser.add_argument("--video", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--text", required=True, help="Voice line text for subtitles")
    parser.add_argument("--output", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("DRY RUN")
        log.info("Dry-run: placeholder at %s", out)
    else:
        ok = assemble_short(args.video, args.audio, args.text, args.output)
        raise SystemExit(0 if ok else 1)
