"""
generate_video.py
=================
Video generation module for GlitchRealityAI.

Supports:
  - Hugging Face Gradio Spaces (primary, free)
  - Kling API (paid upgrade)
  - Runway API (paid upgrade)

Design principle: every paid upgrade is a one-function swap.
To upgrade, set `video.provider` in config.yaml to the desired provider
and add the corresponding API key to GitHub Secrets.

Usage:
    python scripts/generate_video.py --prompt "..." --output /tmp/video.mp4
"""
from __future__ import annotations

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
log = logging.getLogger("scripts.generate_video")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Hugging Face Gradio — free tier
# ---------------------------------------------------------------------------

def _gradio_predict(
    space_url: str,
    api_path: str,
    prompt: str,
    num_frames: int,
    fps: int,
    resolution: str,
    timeout: int,
    hf_token: str | None,
) -> bytes | None:
    """
    Call a HuggingFace Gradio Space via the /run/predict or /api/predict endpoint.
    Returns raw video bytes on success, None on failure.
    """
    width, height = (int(x) for x in resolution.split("x"))
    endpoint = f"{space_url.rstrip('/')}{api_path}"

    payload = {
        "data": [
            prompt,
            num_frames,
            fps,
            width,
            height,
            42,   # seed — randomised below
        ]
    }
    payload["data"][-1] = random.randint(0, 2**31)  # random seed for variety

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    log.info("POST %s (timeout=%ds)", endpoint, timeout)
    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        # Gradio returns {"data": [{"path": "...", "url": "..."}]} or similar
        result = data.get("data", [{}])[0]

        # Some Spaces return a URL, others return base64
        if isinstance(result, dict):
            video_url = result.get("url") or result.get("path")
            if video_url:
                log.info("Downloading video from %s", video_url)
                video_resp = requests.get(video_url, timeout=120, headers=headers)
                video_resp.raise_for_status()
                return video_resp.content
        elif isinstance(result, str) and result.startswith("http"):
            video_resp = requests.get(result, timeout=120)
            video_resp.raise_for_status()
            return video_resp.content

        log.warning("Unexpected Gradio response structure: %s", str(data)[:200])
        return None

    except requests.RequestException as exc:
        log.error("Gradio request failed: %s", exc)
        return None


def generate_video_huggingface(
    prompt: str,
    config: dict[str, Any],
    output_path: Path,
) -> bool:
    """
    Try each configured HF Space in order until one succeeds.
    Retries each Space up to `retry_attempts` times with exponential sleep.

    Returns True on success, False if all spaces fail.
    """
    hf_cfg = config["video"]["huggingface"]
    spaces: list[dict[str, Any]] = hf_cfg["spaces"]
    retry_attempts: int = hf_cfg.get("retry_attempts", 3)
    sleep_min: int = hf_cfg.get("retry_sleep_min", 30)
    sleep_max: int = hf_cfg.get("retry_sleep_max", 120)
    hf_token: str | None = os.getenv("HF_TOKEN")

    num_frames = config["video"]["num_frames"]
    fps = config["video"]["fps"]
    resolution = config["video"]["resolution"]

    for space in spaces:
        url = space["url"]
        api_path = space.get("api_path", "/run/predict")
        timeout = space.get("timeout", 300)

        log.info("Trying Space: %s", url)
        for attempt in range(1, retry_attempts + 1):
            video_bytes = _gradio_predict(
                space_url=url,
                api_path=api_path,
                prompt=prompt,
                num_frames=num_frames,
                fps=fps,
                resolution=resolution,
                timeout=timeout,
                hf_token=hf_token,
            )
            if video_bytes:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(video_bytes)
                log.info("✓ Video saved: %s (%d bytes)", output_path, len(video_bytes))
                return True

            if attempt < retry_attempts:
                sleep_dur = random.randint(sleep_min, sleep_max)
                log.warning(
                    "Attempt %d/%d failed for %s. Sleeping %ds …",
                    attempt, retry_attempts, url, sleep_dur,
                )
                time.sleep(sleep_dur)

        log.error("All %d attempts failed for Space: %s", retry_attempts, url)

    log.error("All HuggingFace Spaces exhausted. Video generation failed.")
    return False


# ---------------------------------------------------------------------------
# Kling API — paid upgrade
# ---------------------------------------------------------------------------

def generate_video_kling(
    prompt: str,
    config: dict[str, Any],  # noqa: ARG001
    output_path: Path,
) -> bool:
    """
    Generate video via Kling API (paid).
    Activate by setting video.provider = "kling" in config.yaml
    and setting KLING_API_KEY in GitHub Secrets.

    Docs: https://klingai.com/api
    """
    api_key = os.getenv("KLING_API_KEY", "")
    if not api_key:
        raise EnvironmentError("KLING_API_KEY not set in environment.")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "prompt": prompt,
        "model": "kling-v1-5",
        "aspect_ratio": "9:16",
        "duration": 5,
    }

    log.info("Submitting Kling generation job …")
    resp = requests.post(
        "https://api.klingai.com/v1/videos/text2video",
        json=payload,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    job_id = resp.json()["task_id"]

    # Poll for completion
    for _ in range(60):
        time.sleep(10)
        status_resp = requests.get(
            f"https://api.klingai.com/v1/videos/{job_id}",
            headers=headers,
            timeout=30,
        )
        status_resp.raise_for_status()
        data = status_resp.json()
        if data["status"] == "completed":
            video_url = data["video_url"]
            video_bytes = requests.get(video_url, timeout=120).content
            output_path.write_bytes(video_bytes)
            log.info("✓ Kling video saved: %s", output_path)
            return True
        if data["status"] == "failed":
            log.error("Kling job failed: %s", data)
            return False

    log.error("Kling job timed out.")
    return False


# ---------------------------------------------------------------------------
# Runway API — paid upgrade
# ---------------------------------------------------------------------------

def generate_video_runway(
    prompt: str,
    config: dict[str, Any],  # noqa: ARG001
    output_path: Path,
) -> bool:
    """
    Generate video via Runway Gen-3 Alpha API (paid).
    Activate by setting video.provider = "runway" in config.yaml.

    Docs: https://docs.runwayml.com/
    """
    api_key = os.getenv("RUNWAY_API_KEY", "")
    if not api_key:
        raise EnvironmentError("RUNWAY_API_KEY not set in environment.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Runway-Version": "2024-11-06",
    }
    payload = {
        "promptText": prompt,
        "model": "gen3a_turbo",
        "ratio": "768:1280",
        "duration": 5,
    }
    resp = requests.post(
        "https://api.dev.runwayml.com/v1/image_to_video",
        json=payload,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    task_id = resp.json()["id"]

    for _ in range(60):
        time.sleep(10)
        poll = requests.get(
            f"https://api.dev.runwayml.com/v1/tasks/{task_id}",
            headers=headers,
            timeout=30,
        )
        poll.raise_for_status()
        data = poll.json()
        if data["status"] == "SUCCEEDED":
            video_url = data["output"][0]
            video_bytes = requests.get(video_url, timeout=120).content
            output_path.write_bytes(video_bytes)
            log.info("✓ Runway video saved: %s", output_path)
            return True
        if data["status"] == "FAILED":
            log.error("Runway task failed: %s", data)
            return False

    log.error("Runway task timed out.")
    return False


# ---------------------------------------------------------------------------
# Dispatcher — the only function you need to call
# ---------------------------------------------------------------------------

PROVIDERS: dict[str, Any] = {
    "huggingface": generate_video_huggingface,
    "kling": generate_video_kling,
    "runway": generate_video_runway,
}


def generate_video(
    prompt: str,
    output_path: Path | str,
    config: dict[str, Any] | None = None,
) -> bool:
    """
    Generate a video from `prompt` and save to `output_path`.

    Provider is read from config.yaml → video.provider.
    Switching providers only requires changing config + adding secrets.

    Args:
        prompt:      Text description of the video scene.
        output_path: Destination path for the generated video file.
        config:      Loaded config dict (loaded from file if None).

    Returns:
        True on success, False on failure.
    """
    if config is None:
        config = load_config()

    output_path = Path(output_path)
    provider = config["video"].get("provider", "huggingface")

    if provider not in PROVIDERS:
        raise ValueError(f"Unknown video provider: '{provider}'. Valid: {list(PROVIDERS)}")

    log.info("Video provider: %s | Output: %s", provider, output_path)
    return PROVIDERS[provider](prompt, config, output_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate a video from a text prompt")
    parser.add_argument("--prompt", required=True, help="Text prompt for video generation")
    parser.add_argument("--output", required=True, help="Output file path (e.g. /tmp/video.mp4)")
    parser.add_argument("--dry-run", action="store_true", help="Skip actual generation, create dummy file")
    args = parser.parse_args()

    if args.dry_run:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("DRY RUN — placeholder video")
        log.info("Dry-run: placeholder created at %s", out)
    else:
        ok = generate_video(args.prompt, args.output)
        raise SystemExit(0 if ok else 1)
