"""
generate_video.py
=================
Video generation module for GlitchRealityAI.

Supports:
  - HF Inference Providers  (primary, free monthly credits — reliable)
  - Hugging Face Gradio Spaces (legacy, free — unreliable)
  - Pexels stock footage   (fallback, free — reliable)
  - Kling API  (paid upgrade)
  - Runway API (paid upgrade)

Design principle: every paid upgrade is a one-function swap.
To upgrade, set `video.provider` in config.yaml to the desired provider
and add the corresponding API key to GitHub Secrets.

Fallback: set `video.fallback_provider` to automatically try another
provider if the primary fails (e.g. huggingface → pexels).

Usage:
    python scripts/generate_video.py --prompt "..." --output /tmp/video.mp4
"""
from __future__ import annotations

import logging
import os
import random
import re
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
# Hugging Face Gradio — free tier (using gradio_client for Gradio v4+)
# ---------------------------------------------------------------------------

def _gradio_generate(
    space_id: str,
    prompt: str,
    timeout: int,
    hf_token: str | None,
) -> bytes | None:
    """
    Call a HuggingFace Gradio Space via gradio_client.
    Returns raw video bytes on success, None on failure.
    """
    try:
        from gradio_client import Client
    except ImportError:
        log.error("gradio_client not installed. Run: pip install gradio-client")
        return None

    try:
        log.info("Connecting to Space: %s (timeout=%ds)", space_id, timeout)
        client = Client(space_id, token=hf_token)

        # Most video gen spaces accept: prompt, negative_prompt, seed, steps, etc.
        # We try the most common API patterns
        seed = random.randint(0, 2**31)

        # Attempt: standard text-to-video predict
        result = client.predict(
            prompt,
            api_name="/generate",  # most common endpoint name
        )

        # result is typically a file path or dict with file path
        if isinstance(result, str) and os.path.isfile(result):
            with open(result, "rb") as f:
                return f.read()
        elif isinstance(result, dict):
            video_path = result.get("video") or result.get("value") or result.get("path")
            if video_path and os.path.isfile(str(video_path)):
                with open(str(video_path), "rb") as f:
                    return f.read()
        elif isinstance(result, tuple):
            # Some spaces return (video_path, ...) tuple
            for item in result:
                if isinstance(item, str) and os.path.isfile(item):
                    with open(item, "rb") as f:
                        return f.read()

        log.warning("Unexpected result type from Space: %s — %s", space_id, type(result))
        return None

    except Exception as exc:
        log.error("gradio_client failed for %s: %s", space_id, exc)
        return None


def _gradio_generate_fallback(
    space_id: str,
    prompt: str,
    timeout: int,
    hf_token: str | None,
) -> bytes | None:
    """
    Fallback: try /predict or /infer endpoint names.
    """
    try:
        from gradio_client import Client
        client = Client(space_id, token=hf_token)

        # Try common alternative endpoint names
        for api_name in ["/predict", "/infer", "/run", "/text2video"]:
            try:
                result = client.predict(prompt, api_name=api_name)
                if isinstance(result, str) and os.path.isfile(result):
                    with open(result, "rb") as f:
                        return f.read()
                if isinstance(result, tuple):
                    for item in result:
                        if isinstance(item, str) and os.path.isfile(item):
                            with open(item, "rb") as f:
                                return f.read()
            except Exception:
                continue

        return None

    except Exception as exc:
        log.error("gradio_client fallback failed for %s: %s", space_id, exc)
        return None


def generate_video_huggingface(
    prompt: str,
    config: dict[str, Any],
    output_path: Path,
) -> bool:
    """
    Try each configured HF Space in order until one succeeds.
    Uses gradio_client for proper Gradio v4+ API compatibility.
    Retries each Space up to `retry_attempts` times with exponential sleep.

    Returns True on success, False if all spaces fail.
    """
    hf_cfg = config["video"]["huggingface"]
    spaces: list[dict[str, Any]] = hf_cfg["spaces"]
    retry_attempts: int = hf_cfg.get("retry_attempts", 3)
    sleep_min: int = hf_cfg.get("retry_sleep_min", 30)
    sleep_max: int = hf_cfg.get("retry_sleep_max", 120)
    hf_token: str | None = os.getenv("HF_TOKEN")

    for space in spaces:
        space_id = space["space_id"]
        timeout = space.get("timeout", 300)

        log.info("Trying Space: %s", space_id)
        for attempt in range(1, retry_attempts + 1):
            video_bytes = _gradio_generate(space_id, prompt, timeout, hf_token)
            if not video_bytes:
                video_bytes = _gradio_generate_fallback(space_id, prompt, timeout, hf_token)

            if video_bytes:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(video_bytes)
                log.info("✓ Video saved: %s (%d bytes)", output_path, len(video_bytes))
                return True

            if attempt < retry_attempts:
                sleep_dur = random.randint(sleep_min, sleep_max)
                log.warning(
                    "Attempt %d/%d failed for %s. Sleeping %ds …",
                    attempt, retry_attempts, space_id, sleep_dur,
                )
                time.sleep(sleep_dur)

        log.error("All %d attempts failed for Space: %s", retry_attempts, space_id)

    log.error("All HuggingFace Spaces exhausted. Video generation failed.")
    return False


# ---------------------------------------------------------------------------
# Pexels stock footage — free, reliable fallback
# ---------------------------------------------------------------------------

# Glitch-in-reality themed queries — atmospheric/surreal stock clips that
# look great with FFmpeg glitch overlay effects applied during montage.
PEXELS_QUERIES: list[str] = [
    "surveillance camera footage",
    "security camera night",
    "time lapse city street",
    "urban night neon lights",
    "foggy forest aerial",
    "abandoned building interior",
    "empty corridor dark",
    "ocean waves slow motion",
    "underwater light rays",
    "lightning storm clouds",
    "subway train moving",
    "highway traffic night",
    "rain window reflection",
    "elevator doors closing",
    "static television screen",
    "parking lot security camera",
    "staircase looking down",
    "tunnel long perspective",
    "crowd walking timelapse",
    "mirror reflection dark",
    "fluorescent light flickering",
    "drone aerial landscape",
    "night sky stars rotating",
    "street lamp fog",
    "shadows moving wall",
]


def _extract_search_terms(prompt: str) -> list[str]:
    """Extract a few short search-friendly terms from a visual prompt."""
    # Remove common filler words, keep nouns and adjectives
    stop = {
        "a", "an", "the", "of", "in", "on", "at", "to", "and", "or", "is",
        "are", "was", "were", "with", "for", "from", "by", "as", "it", "its",
        "this", "that", "very", "so", "just", "but", "into", "out", "up", "down",
        "about", "being", "been", "has", "have", "had", "do", "does", "did",
        "will", "would", "could", "should", "can", "may", "might", "shall",
        "like", "looking", "seems", "through", "while", "where", "there",
    }
    words = re.findall(r"[a-zA-Z]{3,}", prompt.lower())
    keywords = [w for w in words if w not in stop]
    # Build 2-3 word search phrases from the first meaningful words
    queries: list[str] = []
    if len(keywords) >= 2:
        queries.append(f"{keywords[0]} {keywords[1]}")
    if len(keywords) >= 4:
        queries.append(f"{keywords[2]} {keywords[3]}")
    if len(keywords) >= 6:
        queries.append(f"{keywords[4]} {keywords[5]}")
    return queries


def _pexels_best_file(video_files: list[dict]) -> dict | None:
    """Pick the best HD file from Pexels video_files list (portrait for Shorts)."""
    hd = [f for f in video_files if (f.get("height") or 0) >= 720]
    if hd:
        return min(hd, key=lambda f: abs((f.get("height") or 0) - 1920))
    if video_files:
        return max(video_files, key=lambda f: f.get("height") or 0)
    return None


def generate_video_pexels(
    prompt: str,
    config: dict[str, Any],
    output_path: Path,
) -> bool:
    """
    Download a stock video clip from Pexels that matches the prompt.
    Falls back to glitch-themed hardcoded queries if prompt search returns nothing.
    """
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        log.error("PEXELS_API_KEY not set — cannot use Pexels provider.")
        return False

    headers = {"Authorization": api_key}

    # Build query list: prompt-derived first, then hardcoded fallbacks
    queries = _extract_search_terms(prompt)
    base = [q for q in PEXELS_QUERIES if q not in queries]
    random.shuffle(base)
    queries.extend(base)
    queries = queries[:10]  # Try up to 10 queries

    for query in queries:
        try:
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={"query": query, "per_page": 3, "orientation": "portrait"},
                timeout=30,
            )
            if resp.status_code != 200:
                log.warning("Pexels search failed (%d) for query: %s", resp.status_code, query)
                continue

            videos = resp.json().get("videos", [])
            if not videos:
                continue

            # Pick a random video from the results
            video = random.choice(videos)
            best = _pexels_best_file(video.get("video_files", []))
            if not best or not best.get("link"):
                continue

            # Download the clip
            dl = requests.get(best["link"], timeout=120, stream=True)
            dl.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("wb") as f:
                for chunk in dl.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            log.info(
                "✓ Pexels clip saved: %s (query=%r, %dx%d)",
                output_path,
                query,
                best.get("width", 0),
                best.get("height", 0),
            )
            return True

        except Exception as exc:
            log.warning("Pexels query %r failed: %s", query, exc)
            continue

    log.error("Pexels: no suitable clips found across all queries.")
    return False


# ---------------------------------------------------------------------------
# AI Images + Ken Burns — free text-to-image via HF Inference → animated video
# ---------------------------------------------------------------------------

_IMAGE_PROMPT_MODIFIERS: list[str] = [
    "",                                     # original prompt
    "close-up shot, ",
    "wide angle, ",
    "dramatic lighting, ",
    "low angle perspective, ",
    "cinematic, film still, ",
    "atmospheric, misty, ",
    "neon-lit, ",
    "moody silhouette, ",
    "overhead bird-eye view, ",
]


def _make_prompt_variations(base_prompt: str, count: int) -> list[str]:
    """Create slight visual variations of a prompt for image diversity."""
    mods = list(_IMAGE_PROMPT_MODIFIERS)
    random.shuffle(mods)
    return [f"{mods[i % len(mods)]}{base_prompt}" for i in range(count)]


def generate_video_ai_images(
    prompt: str,
    config: dict[str, Any],
    output_path: Path,
) -> bool:
    """
    Generate AI images via HF text_to_image, then assemble a Ken Burns
    slideshow video.  Uses free models like FLUX.1-schnell on HF Inference.

    This is the most cost-effective AI-generated content approach:
    text_to_image is free on HF native inference for many models.
    """
    try:
        from huggingface_hub import InferenceClient
    except ImportError:
        log.error("huggingface_hub not installed.")
        return False

    hf_token: str | None = os.getenv("HF_TOKEN")
    if not hf_token:
        log.error("HF_TOKEN not set — cannot use AI image generation.")
        return False

    ai_cfg = config["video"].get("ai_images", {})
    models: list[str] = ai_cfg.get("models", ["black-forest-labs/FLUX.1-schnell"])
    num_images: int = ai_cfg.get("num_images", 4)
    img_w: int = ai_cfg.get("width", 576)
    img_h: int = ai_cfg.get("height", 1024)
    dur_per_img: float = ai_cfg.get("duration_per_image", 3.0)

    client = InferenceClient(api_key=hf_token)
    variations = _make_prompt_variations(prompt, num_images)

    saved_images: list[Path] = []
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for i, var_prompt in enumerate(variations):
        for model_id in models:
            try:
                log.info(
                    "AI image %d/%d: model=%s prompt=%.80s…",
                    i + 1, num_images, model_id, var_prompt,
                )
                image = client.text_to_image(
                    var_prompt,
                    model=model_id,
                    width=img_w,
                    height=img_h,
                )
                img_path = output_path.parent / f"_ai_img_{i}.png"
                image.save(str(img_path))
                saved_images.append(img_path)
                log.info("✓ AI image %d/%d saved (%s)", i + 1, num_images, model_id)
                break  # success — move to next variation
            except Exception as exc:
                log.warning("Image gen failed (%s): %s", model_id, exc)
                continue

    if not saved_images:
        log.error("Failed to generate any AI images.")
        return False

    log.info("Generated %d/%d AI images — building Ken Burns video …", len(saved_images), num_images)

    # Build animated slideshow
    from scripts.montage import build_kenburns_video

    ok = build_kenburns_video(
        saved_images,
        output_path,
        duration_per_image=dur_per_img,
    )

    # Cleanup temp images
    for img in saved_images:
        img.unlink(missing_ok=True)

    return ok


# ---------------------------------------------------------------------------
# HF Inference Providers — free monthly credits, serverless (fal-ai, Replicate…)
# ---------------------------------------------------------------------------

def generate_video_hf_inference(
    prompt: str,
    config: dict[str, Any],
    output_path: Path,
) -> bool:
    """
    Generate video via HuggingFace Inference Providers (text_to_video).

    Uses InferenceClient routed through HF to serverless providers like
    fal-ai and Replicate.  Billing goes to the HF account (free monthly
    credits for HF users).  Tries each configured provider+model combo
    in order until one succeeds.

    Returns True on success, False if all combos fail.
    """
    try:
        from huggingface_hub import InferenceClient
    except ImportError:
        log.error("huggingface_hub not installed. Run: pip install huggingface-hub")
        return False

    hf_token: str | None = os.getenv("HF_TOKEN")
    if not hf_token:
        log.error("HF_TOKEN not set — cannot use HF Inference Providers.")
        return False

    inf_cfg = config["video"].get("hf_inference", {})
    combos: list[dict[str, str]] = inf_cfg.get("provider_models", [])
    timeout: int = inf_cfg.get("timeout", 300)

    if not combos:
        log.error("No provider_models configured under video.hf_inference.")
        return False

    for combo in combos:
        provider = combo["provider"]
        model_id = combo["model"]
        log.info(
            "HF Inference: trying provider=%s model=%s (timeout=%ds)",
            provider, model_id, timeout,
        )
        try:
            client = InferenceClient(
                provider=provider,
                api_key=hf_token,
                timeout=timeout,
            )
            video_bytes: bytes = client.text_to_video(
                prompt,
                model=model_id,
            )
            if video_bytes:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(video_bytes)
                log.info(
                    "✓ HF Inference video saved: %s (%d bytes, provider=%s, model=%s)",
                    output_path, len(video_bytes), provider, model_id,
                )
                return True
            log.warning("HF Inference returned empty bytes for %s/%s", provider, model_id)
        except Exception as exc:
            log.warning(
                "HF Inference failed for %s/%s: %s",
                provider, model_id, exc,
            )
            continue

    log.error("All HF Inference provider+model combos exhausted.")
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
    "ai_images": generate_video_ai_images,
    "hf_inference": generate_video_hf_inference,
    "huggingface": generate_video_huggingface,
    "pexels": generate_video_pexels,
    "kling": generate_video_kling,
    "runway": generate_video_runway,
}


def generate_video(
    prompt: str,
    output_path: Path | str,
    config: dict[str, Any] | None = None,
) -> bool:
    """
    Generate a video from *prompt* and save to *output_path*.

    Supports two strategies (``video.strategy`` in config.yaml):

    * **single** (default) — use ``provider``, then ``fallback_provider``.
    * **hybrid** — randomly choose from ``hybrid_providers`` (weighted).
      On failure, try remaining providers in-order; ``fallback_provider``
      is always the last safety net.
    """
    if config is None:
        config = load_config()

    output_path = Path(output_path)
    strategy = config["video"].get("strategy", "single")
    fallback = config["video"].get("fallback_provider", "")

    # ------------------------------------------------------------------
    # Hybrid: weighted random selection, then cascade
    # ------------------------------------------------------------------
    if strategy == "hybrid":
        hybrid_cfg: list[dict[str, Any]] = config["video"].get("hybrid_providers", [])
        if hybrid_cfg:
            names = [p["provider"] for p in hybrid_cfg]
            weights = [p.get("weight", 1) for p in hybrid_cfg]

            # Build a weighted-random order (chosen first, rest shuffled)
            chosen = random.choices(names, weights=weights, k=1)[0]
            order = [chosen] + [n for n in names if n != chosen]

            tried: set[str] = set()
            for name in order:
                if name in tried or name not in PROVIDERS:
                    continue
                tried.add(name)
                log.info("Hybrid strategy: trying provider '%s'", name)
                if PROVIDERS[name](prompt, config, output_path):
                    return True
                log.warning("Hybrid provider '%s' failed.", name)

            # Ultimate fallback
            if fallback and fallback not in tried and fallback in PROVIDERS:
                log.warning("Hybrid exhausted — trying fallback '%s'", fallback)
                if PROVIDERS[fallback](prompt, config, output_path):
                    return True

            log.error("All hybrid providers exhausted.")
            return False

        # No hybrid_providers configured — fall through to single mode
        log.warning("strategy=hybrid but no hybrid_providers — using single mode.")

    # ------------------------------------------------------------------
    # Single: primary → fallback
    # ------------------------------------------------------------------
    provider = config["video"].get("provider", "huggingface")
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown video provider: '{provider}'. Valid: {list(PROVIDERS)}")

    log.info("Video provider: %s | Output: %s", provider, output_path)
    ok = PROVIDERS[provider](prompt, config, output_path)

    if not ok and fallback and fallback in PROVIDERS and fallback != provider:
        log.warning("Primary provider '%s' failed — trying fallback '%s'", provider, fallback)
        ok = PROVIDERS[fallback](prompt, config, output_path)

    return ok


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
