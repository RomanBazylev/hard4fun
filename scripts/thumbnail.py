"""
thumbnail.py
============
Thumbnail generator for GlitchRealityAI.

Extracts the most visually interesting frame from the generated video
and overlays bold "REALITY GLITCH" text with emoji branding.

Requires: ffmpeg, Pillow

Usage:
    python scripts/thumbnail.py \
        --video /tmp/final.mp4 \
        --text  "This glitch broke reality" \
        --output /tmp/thumbnail.jpg
"""
from __future__ import annotations

import logging
import random
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "config.yaml"
FONT_DEFAULT = ROOT / "assets" / "fonts" / "Impact.ttf"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("scripts.thumbnail")


def load_config() -> dict[str, Any]:
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f)


def get_video_duration(video_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 3.0


def extract_frame(video_path: Path, timestamp: float, output: Path) -> bool:
    """Extract a single frame from the video at `timestamp` seconds."""
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-ss", str(timestamp),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("Frame extraction failed: %s", result.stderr[-500:])
        return False
    return True


def add_text_overlay(
    frame_path: Path,
    hook_text: str,
    output_path: Path,
    font_path: str,
) -> bool:
    """
    Use ffmpeg to add a dramatic text overlay to the thumbnail frame.
    Top line: "REALITY GLITCH ⚠️"
    Bottom area: hook text
    """
    title_line = "REALITY GLITCH \\u26a0"
    # Truncate hook text for thumbnail
    hook_short = hook_text[:50] + "…" if len(hook_text) > 50 else hook_text
    # Escape special chars
    for ch in ["\\", ":", "'", "[", "]"]:
        hook_short = hook_short.replace(ch, "\\" + ch)
        title_line = title_line.replace(ch, "\\" + ch)

    top_text = (
        f"drawtext=text='{title_line}'"
        f":fontfile='{font_path}'"
        f":fontsize=90"
        f":fontcolor=yellow"
        f":bordercolor=red"
        f":borderw=4"
        f":x=(w-text_w)/2"
        f":y=80"
    )
    bottom_text = (
        f"drawtext=text='{hook_short}'"
        f":fontfile='{font_path}'"
        f":fontsize=52"
        f":fontcolor=white"
        f":bordercolor=black"
        f":borderw=3"
        f":x=(w-text_w)/2"
        f":y=h-text_h-120"
    )

    # Add semi-transparent black bar behind top text
    overlay_filter = (
        f"drawbox=x=0:y=60:w=iw:h=120:color=black@0.5:t=fill,"
        f"{top_text},{bottom_text}"
    )

    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(frame_path),
            "-vf", overlay_filter,
            "-q:v", "2",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("Text overlay failed: %s", result.stderr[-500:])
        return False
    return True


def generate_thumbnail(
    video_path: Path | str,
    hook_text: str,
    output_path: Path | str,
    config: dict[str, Any] | None = None,
) -> bool:
    """
    Generate a YouTube thumbnail from the video.

    Picks a frame from the first 30% of the video (usually the most
    visually striking part of AI-generated content) and overlays
    dramatic glitch-branded text.

    Args:
        video_path:  Path to the final assembled Short video.
        hook_text:   The hook/caption text to display.
        output_path: Where to save the .jpg thumbnail.
        config:      Loaded config dict (loaded from file if None).

    Returns:
        True on success.
    """
    if config is None:
        config = load_config()

    video_path = Path(video_path)
    output_path = Path(output_path)

    if not video_path.exists():
        log.error("Video not found: %s", video_path)
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    duration = get_video_duration(video_path)
    # Pick a frame from the first 30% of the clip (usually most striking)
    timestamp = random.uniform(0.5, max(1.0, duration * 0.3))

    font_cfg = config["montage"].get("font", str(FONT_DEFAULT))
    font_path = Path(font_cfg) if not Path(font_cfg).is_absolute() else Path(font_cfg)
    if not font_path.is_absolute():
        font_path = ROOT / font_path
    if not font_path.exists():
        # Try system fonts
        for sf in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]:
            if Path(sf).exists():
                font_path = Path(sf)
                break

    with tempfile.TemporaryDirectory(prefix="glitch_thumb_") as tmp_dir:
        raw_frame = Path(tmp_dir) / "frame.jpg"
        if not extract_frame(video_path, timestamp, raw_frame):
            return False

        if not add_text_overlay(raw_frame, hook_text, output_path, str(font_path)):
            # Fallback: just save the raw frame as thumbnail
            log.warning("Text overlay failed — saving plain frame as thumbnail.")
            import shutil
            shutil.copy(raw_frame, output_path)

    file_size = output_path.stat().st_size / 1024
    log.info("✓ Thumbnail: %s (%.1f KB)", output_path, file_size)
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate YouTube thumbnail from video")
    parser.add_argument("--video", required=True)
    parser.add_argument("--text", required=True, help="Hook text for overlay")
    parser.add_argument("--output", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"DRY RUN")
        log.info("Dry-run: placeholder at %s", out)
    else:
        ok = generate_thumbnail(args.video, args.text, args.output)
        raise SystemExit(0 if ok else 1)
