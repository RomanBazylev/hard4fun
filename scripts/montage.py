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


def ensure_music_available() -> None:
    """
    Generate a dark ambient background track using FFmpeg if the
    assets/music/ directory is empty.  No downloads needed.
    """
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    existing = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
    if existing:
        return

    dest = MUSIC_DIR / "ambient_drone.mp3"
    # Generate a 120-second dark ambient drone with FFmpeg:
    # - Low sine wave (55 Hz) as a bass hum
    # - Layered with filtered brown noise for texture
    # - Faded in/out for smooth looping
    ok = run_ffmpeg(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            "sine=frequency=55:duration=120,volume=0.3",
            "-f", "lavfi", "-i",
            "anoisesrc=duration=120:color=brown,lowpass=f=200,volume=0.15",
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:duration=first,"
            "afade=t=in:st=0:d=3,afade=t=out:st=117:d=3",
            "-c:a", "libmp3lame", "-b:a", "128k",
            str(dest),
        ],
        label="generate_ambient",
    )
    if ok and dest.exists():
        log.info("✓ Ambient track generated: %s (%.1f KB)", dest, dest.stat().st_size / 1024)
    else:
        log.warning("Failed to generate ambient track — continuing without music.")


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


def _load_word_timings(audio_path: Path) -> list[dict] | None:
    """Load word timings from the .words.json sidecar file."""
    timings_path = audio_path.with_suffix(".words.json")
    if not timings_path.exists():
        return None
    try:
        import json as _json
        with timings_path.open(encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return None


def build_subtitle_filter(
    text: str,
    config: dict[str, Any],
    font_path: str,
    video_duration: float,
    audio_path: Path | None = None,
) -> str:
    """
    Build karaoke-style subtitles if word timings are available,
    otherwise fall back to static full-text subtitles.
    """
    style = config["montage"]["subtitle_style"]
    fontsize = style.get("fontsize", 48)
    fontcolor = style.get("fontcolor", "white")
    highlight_color = style.get("highlight_color", "yellow")
    outline_color = style.get("outline_color", "black")
    outline_w = style.get("outline_width", 3)

    word_timings = _load_word_timings(audio_path) if audio_path else None

    if word_timings and len(word_timings) >= 2:
        return _build_karaoke_filter(
            word_timings, fontsize, fontcolor, highlight_color,
            outline_color, outline_w, font_path, video_duration,
        )

    return _build_static_subtitle(
        text, fontsize, fontcolor, outline_color, outline_w,
        font_path, video_duration,
    )


def _build_karaoke_filter(
    word_timings: list[dict],
    fontsize: int,
    fontcolor: str,
    highlight_color: str,
    outline_color: str,
    outline_w: int,
    font_path: str,
    video_duration: float,
) -> str:
    """
    Build karaoke subtitles: show 2-4 words at a time, centred,
    with the current word highlighted in yellow above.
    """
    max_words_per_chunk = 4
    max_chars = 28
    chunks: list[list[dict]] = []
    current_chunk: list[dict] = []
    current_len = 0

    for wt in word_timings:
        word_len = len(wt["word"]) + 1
        too_many = len(current_chunk) >= max_words_per_chunk
        too_wide = current_len + word_len > max_chars and current_chunk

        if too_many or too_wide:
            chunks.append(current_chunk)
            current_chunk = [wt]
            current_len = word_len
        else:
            current_chunk.append(wt)
            current_len += word_len
    if current_chunk:
        chunks.append(current_chunk)

    filters: list[str] = []

    for chunk in chunks:
        if not chunk:
            continue
        chunk_start = chunk[0]["start"]
        chunk_end = min(chunk[-1]["end"] + 0.2, video_duration)

        # Base layer: full chunk text in semi-transparent white
        chunk_text = " ".join(w["word"] for w in chunk)
        escaped_chunk = _escape_drawtext(chunk_text)

        # Base layer: full chunk text in solid white at the bottom
        filters.append(
            f"drawtext=text='{escaped_chunk}'"
            f":fontfile='{font_path}'"
            f":fontsize={fontsize}"
            f":fontcolor={fontcolor}"
            f":bordercolor={outline_color}"
            f":borderw={outline_w + 1}"
            f":x=(w-text_w)/2"
            f":y='h-text_h-60'"
            f":enable='between(t,{chunk_start:.3f},{chunk_end:.3f})'"
        )

        # Highlight layer: current word in yellow, larger, above base line
        for wt in chunk:
            escaped_word = _escape_drawtext(wt["word"])
            w_start = wt["start"]
            w_end = min(wt["end"], video_duration)

            filters.append(
                f"drawtext=text='{escaped_word}'"
                f":fontfile='{font_path}'"
                f":fontsize={int(fontsize * 1.2)}"
                f":fontcolor={highlight_color}"
                f":bordercolor={outline_color}"
                f":borderw={outline_w + 2}"
                f":x=(w-text_w)/2"
                f":y='h-text_h-60-{fontsize + 14}'"
                f":enable='between(t,{w_start:.3f},{w_end:.3f})'"
            )

    if not filters:
        return _build_static_subtitle(
            " ".join(w["word"] for w in word_timings),
            fontsize, fontcolor, outline_color, outline_w,
            font_path, video_duration,
        )

    return ",".join(filters)


def _build_static_subtitle(
    text: str,
    fontsize: int,
    fontcolor: str,
    outline_color: str,
    outline_w: int,
    font_path: str,
    video_duration: float,
) -> str:
    """Build a static full-text drawtext filter (fallback)."""
    max_chars = 28
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > max_chars:
            lines.append(current.strip())
            current = word + " "
        else:
            current += word + " "
    if current.strip():
        lines.append(current.strip())

    if len(lines) > 3:
        lines = lines[:3]
        lines[-1] = lines[-1][:max_chars - 1] + "…"

    escaped_text = "\n".join(_escape_drawtext(line) for line in lines)

    return (
        f"drawtext=text='{escaped_text}'"
        f":fontfile='{font_path}'"
        f":fontsize={fontsize}"
        f":fontcolor={fontcolor}"
        f":bordercolor={outline_color}"
        f":borderw={outline_w + 1}"
        f":x=(w-text_w)/2"
        f":y='h-text_h-60'"
        f":enable='between(t,0,{video_duration})'"
    )


# ---------------------------------------------------------------------------
# Ken Burns slideshow — animated video from AI images
# ---------------------------------------------------------------------------

# Zoom/pan effects that avoid commas in FFmpeg expressions
_KENBURNS_EFFECTS: list[dict[str, str]] = [
    # Slow zoom-in from centre
    {"z": "1+0.005*on", "x": "iw/2-(iw/zoom/2)", "y": "ih/2-(ih/zoom/2)"},
    # Slow zoom-out from centre
    {"z": "1.5-0.005*on", "x": "iw/2-(iw/zoom/2)", "y": "ih/2-(ih/zoom/2)"},
    # Pan left→right at steady zoom
    {"z": "1.3", "x": "on*(iw-iw/zoom)/{d}", "y": "ih/2-(ih/zoom/2)"},
    # Pan right→left at steady zoom
    {"z": "1.3", "x": "(iw-iw/zoom)-on*(iw-iw/zoom)/{d}", "y": "ih/2-(ih/zoom/2)"},
    # Zoom-in + pan down
    {"z": "1+0.004*on", "x": "iw/2-(iw/zoom/2)", "y": "on*(ih-ih/zoom)/{d}"},
    # Zoom-in + pan up
    {"z": "1+0.004*on", "x": "iw/2-(iw/zoom/2)", "y": "(ih-ih/zoom)-on*(ih-ih/zoom)/{d}"},
]


def build_kenburns_video(
    image_paths: list[Path],
    output_path: Path,
    duration_per_image: float = 3.0,
    fps: int = 25,
    output_w: int = 1080,
    output_h: int = 1920,
) -> bool:
    """
    Build a Ken Burns slideshow video from a list of images.

    Each image is scaled to a canvas 1.5× the output size, then animated
    with a random zoom/pan effect.  Clips are concatenated into a single
    video that can be fed directly into the normal montage pipeline.

    Returns True on success.
    """
    if not image_paths:
        log.error("No images provided for Ken Burns slideshow.")
        return False

    frames_per_image = int(duration_per_image * fps)
    canvas_w = int(output_w * 1.5)
    canvas_h = int(output_h * 1.5)

    with tempfile.TemporaryDirectory(prefix="kenburns_") as tmp_dir:
        tmp = Path(tmp_dir)
        clips: list[Path] = []

        used_indices: list[int] = []
        for i, img in enumerate(image_paths):
            # Pick a different effect for consecutive images
            avail = [j for j in range(len(_KENBURNS_EFFECTS)) if j not in used_indices]
            if not avail:
                avail = list(range(len(_KENBURNS_EFFECTS)))
                used_indices.clear()
            idx = random.choice(avail)
            used_indices.append(idx)
            eff = _KENBURNS_EFFECTS[idx]

            z_expr = eff["z"]
            x_expr = eff["x"].replace("{d}", str(frames_per_image))
            y_expr = eff["y"].replace("{d}", str(frames_per_image))

            vf = (
                f"scale={canvas_w}:{canvas_h}:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}'"
                f":d={frames_per_image}:s={output_w}x{output_h}:fps={fps},"
                f"format=yuv420p"
            )

            clip_path = tmp / f"clip_{i}.mp4"
            ok = run_ffmpeg(
                [
                    "ffmpeg", "-y",
                    "-loop", "1", "-i", str(img),
                    "-vf", vf,
                    "-t", str(duration_per_image),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    str(clip_path),
                ],
                label=f"kenburns_{i}",
            )
            if ok and clip_path.exists():
                clips.append(clip_path)
            else:
                log.warning("Ken Burns clip %d failed — skipping image.", i)

        if not clips:
            log.error("All Ken Burns clips failed.")
            return False

        if len(clips) == 1:
            shutil.copy(clips[0], output_path)
            log.info("✓ Ken Burns video (single clip): %s", output_path)
            return True

        # Concatenate clips via concat demuxer
        concat_list = tmp / "concat.txt"
        with concat_list.open("w") as f:
            for clip in clips:
                f.write(f"file '{clip}'\n")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        ok = run_ffmpeg(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                str(output_path),
            ],
            label="kenburns_concat",
        )
        if ok:
            log.info("✓ Ken Burns video: %s (%d clips)", output_path, len(clips))
        return ok


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

    # For Shorts: video length = audio + small padding (not silent 12s video)
    dur_min = config["channel"].get("video_duration_min", 5)
    dur_max = config["channel"].get("video_duration_max", 12)
    target_dur = max(aud_dur + 1.5, dur_min)  # audio + 1.5s breathing room
    target_dur = min(target_dur, dur_max)

    log.info("Assembling: video=%.1fs audio=%.1fs target=%.1fs", vid_dur, aud_dur, target_dur)

    font_path = resolve_font(config)
    music_track = pick_music_track()
    if music_track is None:
        ensure_music_available()
        music_track = pick_music_track()
    glitch_enabled = config["montage"].get("glitch_overlay", True)
    music_vol = config["montage"].get("music_volume", 0.15)

    with tempfile.TemporaryDirectory(prefix="glitch_montage_") as tmp_dir:
        tmp = Path(tmp_dir)

        # Step 1: Scale + pad to 1080×1920 (9:16)
        # If stock clip is shorter than target, loop it; if longer, trim it
        scaled = tmp / "scaled.mp4"
        scale_filter = (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
        )
        input_args = ["-y"]
        if vid_dur < target_dur:
            # Loop the stock clip to cover the target duration
            input_args += ["-stream_loop", "-1"]
        input_args += ["-i", str(video_path)]
        ok = run_ffmpeg(
            [
                "ffmpeg", *input_args,
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

        # Step 3: Burn subtitles (karaoke if word timings available)
        subtitled = tmp / "subtitled.mp4"
        sub_filter = build_subtitle_filter(
            voice_text, config, font_path, target_dur, audio_path=audio_path,
        )
        # Write filter to file — avoids arg-length issues with 100+ drawtext layers
        sub_script = tmp / "subtitle.filter"
        sub_script.write_text(sub_filter, encoding="utf-8")
        ok = run_ffmpeg(
            [
                "ffmpeg", "-y", "-i", str(glitched),
                "-filter_script:v", str(sub_script),
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
