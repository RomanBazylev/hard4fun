"""
upload.py
=========
YouTube Data API v3 upload module for GlitchRealityAI.

Handles:
  - OAuth2 refresh token flow (no interactive browser needed in CI)
  - Video upload with metadata (title, description, tags, thumbnail)
  - SEO-optimised auto-generated titles and descriptions
  - Retry logic for transient network errors
  - Dry-run mode

Required secrets (GitHub Actions / environment):
  YOUTUBE_CLIENT_ID
  YOUTUBE_CLIENT_SECRET
  YOUTUBE_REFRESH_TOKEN

See README.md for step-by-step OAuth setup instructions.

Usage:
    python scripts/upload.py \
        --video /tmp/final.mp4 \
        --thumbnail /tmp/thumb.jpg \
        --title "This Glitch Broke Reality 😱" \
        --hook "The simulation made gravity optional today"
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "config.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("scripts.upload")

YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_THUMBNAIL_URL = "https://www.googleapis.com/youtube/v3/thumbnails/set"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# OAuth2 helpers
# ---------------------------------------------------------------------------

def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> str:
    """
    Exchange a refresh token for a short-lived access token.
    Called before every upload to ensure the token is fresh.
    """
    resp = requests.post(
        YOUTUBE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(f"Token refresh failed: {data}")
    log.info("Access token refreshed (expires_in=%ds)", data.get("expires_in", 0))
    return access_token


def get_credentials() -> tuple[str, str, str]:
    """Read OAuth credentials from environment variables."""
    client_id = os.environ["YOUTUBE_CLIENT_ID"]
    client_secret = os.environ["YOUTUBE_CLIENT_SECRET"]
    refresh_token = os.environ["YOUTUBE_REFRESH_TOKEN"]
    return client_id, client_secret, refresh_token


# ---------------------------------------------------------------------------
# SEO helpers
# ---------------------------------------------------------------------------

TITLE_TEMPLATES = [
    "This Glitch Just Broke Reality 😱 #{hook_short} #shorts",
    "The Simulation Made an Error 🔴 #{tag} #shorts",
    "Wait… This Shouldn't Be Possible 😳 #shorts",
    "Reality.exe Has Stopped Working 💀 #shorts",
    "The Matrix Glitched AGAIN 👁️ #shorts",
    "When the Simulation Forgets Physics ⚡ #shorts",
    "Is This a Glitch or Are We Dreaming? 🌀 #shorts",
    "This Glitch Will Break Your Brain 🧠 #shorts",
    "They Forgot to Patch This Glitch 🕹️ #shorts",
    "Someone Pressed Ctrl+Z on Reality 🔄 #shorts",
]

DESCRIPTION_TEMPLATE = """{hook}

Reality glitches happen when the simulation forgets the rules. 🔴

Subscribe for daily reality errors, simulation glitches, and mind-bending moments.

📌 Watch more glitches: https://www.youtube.com/@GlitchRealityAI/shorts

🔔 Hit the bell for daily glitch alerts!

{hashtags}

#GlitchRealityAI #SimulationGlitch #RealityGlitch #Shorts
"""


def build_title(hook: str) -> str:
    """Generate a click-worthy title with rotating templates."""
    template = random.choice(TITLE_TEMPLATES)
    hook_short = hook[:30].split()[0] if hook else "reality"
    tag = hook_short.lower().replace(" ", "")
    return template.format(hook_short=hook_short, tag=tag)[:100]  # YT limit: 100 chars


def build_description(hook: str, tags: list[str]) -> str:
    """Build the video description with hashtags."""
    hashtags = " ".join(f"#{t}" for t in tags[:15])
    return DESCRIPTION_TEMPLATE.format(hook=hook, hashtags=hashtags)


def build_tags(config: dict[str, Any], extra: list[str] | None = None) -> list[str]:
    """Combine default tags from config + any extra tags."""
    base = list(config["youtube"]["default_tags"])
    if extra:
        base.extend(extra)
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for t in base:
        if t.lower() not in seen:
            seen.add(t.lower())
            result.append(t)
    return result[:20]  # YouTube max: 500 chars total, ~20 tags


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_video(
    video_path: Path,
    access_token: str,
    title: str,
    description: str,
    tags: list[str],
    config: dict[str, Any],
) -> str | None:
    """
    Upload a video to YouTube using resumable upload.

    Returns the YouTube video ID on success, None on failure.
    """
    yt_cfg = config["youtube"]
    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": yt_cfg.get("category_id", "22"),
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": yt_cfg.get("privacy_status", "public"),
            "selfDeclaredMadeForKids": yt_cfg.get("made_for_kids", False),
        },
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(video_path.stat().st_size),
    }

    # Step 1: Initiate resumable upload
    init_resp = requests.post(
        YOUTUBE_UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers=headers,
        json=metadata,
        timeout=30,
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers.get("Location")
    if not upload_url:
        log.error("No upload URL in response headers: %s", dict(init_resp.headers))
        return None

    # Step 2: Stream the file
    file_size = video_path.stat().st_size
    log.info("Uploading %s (%.1f MB) …", video_path.name, file_size / 1024 / 1024)

    with video_path.open("rb") as f:
        upload_resp = requests.put(
            upload_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "video/mp4",
                "Content-Length": str(file_size),
            },
            data=f,
            timeout=600,
        )

    if upload_resp.status_code not in (200, 201):
        log.error("Upload failed (%d): %s", upload_resp.status_code, upload_resp.text[:500])
        return None

    video_id = upload_resp.json().get("id")
    log.info("✓ Video uploaded! ID: %s | URL: https://youtu.be/%s", video_id, video_id)
    return video_id


def set_thumbnail(
    video_id: str,
    thumbnail_path: Path,
    access_token: str,
) -> bool:
    """Upload a custom thumbnail for the video."""
    if not thumbnail_path.exists():
        log.warning("Thumbnail not found: %s — skipping.", thumbnail_path)
        return False

    with thumbnail_path.open("rb") as f:
        resp = requests.post(
            YOUTUBE_THUMBNAIL_URL,
            params={"videoId": video_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "image/jpeg",
            },
            data=f,
            timeout=60,
        )
    if resp.status_code in (200, 201):
        log.info("✓ Thumbnail set for video %s", video_id)
        return True
    log.warning("Thumbnail upload failed (%d): %s", resp.status_code, resp.text[:200])
    return False


# ---------------------------------------------------------------------------
# Main dispatcher (with retry)
# ---------------------------------------------------------------------------

def upload_short(
    video_path: Path | str,
    thumbnail_path: Path | str | None,
    hook: str,
    extra_tags: list[str] | None = None,
    config: dict[str, Any] | None = None,
    max_retries: int = 3,
) -> str | None:
    """
    Full upload pipeline: token refresh → upload → thumbnail → return video ID.

    Args:
        video_path:     Final assembled Short (.mp4).
        thumbnail_path: Thumbnail image (.jpg). Optional.
        hook:           Hook/caption text for SEO generation.
        extra_tags:     Additional tags beyond config defaults.
        config:         Loaded config (loaded from file if None).
        max_retries:    Number of upload attempts before giving up.

    Returns:
        YouTube video ID string, or None on failure.
    """
    if config is None:
        config = load_config()

    video_path = Path(video_path)
    thumbnail_path = Path(thumbnail_path) if thumbnail_path else None

    client_id, client_secret, refresh_token = get_credentials()

    title = build_title(hook)
    tags = build_tags(config, extra_tags)
    description = build_description(hook, tags)

    log.info("Preparing upload: '%s'", title)

    for attempt in range(1, max_retries + 1):
        try:
            access_token = refresh_access_token(client_id, client_secret, refresh_token)
            video_id = upload_video(video_path, access_token, title, description, tags, config)
            if video_id:
                if thumbnail_path:
                    set_thumbnail(video_id, thumbnail_path, access_token)
                return video_id
        except requests.RequestException as exc:
            log.error("Upload attempt %d/%d failed: %s", attempt, max_retries, exc)
            if attempt < max_retries:
                sleep_dur = 30 * attempt
                log.info("Retrying in %ds …", sleep_dur)
                time.sleep(sleep_dur)

    log.error("All %d upload attempts failed for %s", max_retries, video_path.name)
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Upload a Short to YouTube")
    parser.add_argument("--video", required=True)
    parser.add_argument("--thumbnail", default=None)
    parser.add_argument("--title", default=None, help="Override auto-generated title")
    parser.add_argument("--hook", required=True, help="Hook text for SEO generation")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        log.info("DRY RUN — would upload: %s", args.video)
        log.info("  Hook: %s", args.hook)
        log.info("  Title: %s", build_title(args.hook))
        log.info("  Tags: %s", build_tags(load_config()))
    else:
        video_id = upload_short(
            video_path=args.video,
            thumbnail_path=args.thumbnail,
            hook=args.hook,
        )
        if not video_id:
            raise SystemExit(1)
        print(video_id)
