"""
tts.py
======
Text-to-Speech module for GlitchRealityAI.

Primary (free): edge-tts (Microsoft Edge TTS via unofficial API)
Upgrade path:   ElevenLabs → change tts.provider in config.yaml

Usage:
    python scripts/tts.py --text "The simulation broke." --output /tmp/audio.mp3
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "config.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("scripts.tts")


def load_config() -> dict[str, Any]:
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# edge-tts (FREE — Microsoft Edge TTS)
# ---------------------------------------------------------------------------

async def _edge_tts_async(
    text: str,
    output_path: Path,
    voice: str,
    rate: str,
    pitch: str,
) -> bool:
    """Async core for edge-tts with word-level timing extraction for karaoke."""
    try:
        import edge_tts  # type: ignore[import]
    except ImportError:
        log.error("edge-tts not installed. Run: pip install edge-tts")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # MUST pass boundary="WordBoundary" — default is SentenceBoundary
    # which never yields per-word events.
    communicate = edge_tts.Communicate(
        text, voice, rate=rate, pitch=pitch, boundary="WordBoundary",
    )

    word_timings: list[dict] = []
    audio_chunks: list[bytes] = []

    try:
        async for event in communicate.stream():
            if event["type"] == "audio":
                audio_chunks.append(event["data"])
            elif event["type"] == "WordBoundary":
                # offset and duration are in 100-nanosecond ticks
                word_timings.append({
                    "word": event["text"],
                    "start": event["offset"] / 10_000_000,
                    "end": (event["offset"] + event["duration"]) / 10_000_000,
                })

        # Save audio
        with output_path.open("wb") as f:
            for chunk in audio_chunks:
                f.write(chunk)

        # Save word timings as sidecar JSON (same name, .words.json)
        if word_timings:
            import json as _json
            timings_path = output_path.with_suffix(".words.json")
            with timings_path.open("w", encoding="utf-8") as f:
                _json.dump(word_timings, f, ensure_ascii=False, indent=2)
            log.info(
                "✓ edge-tts saved: %s (voice=%s, %d word timings)",
                output_path, voice, len(word_timings),
            )
        else:
            log.info("✓ edge-tts saved: %s (voice=%s, no word timings)", output_path, voice)

        return True
    except Exception as exc:  # noqa: BLE001
        log.error("edge-tts failed: %s", exc)
        return False


def generate_tts_edge(
    text: str,
    output_path: Path,
    config: dict[str, Any],
) -> bool:
    """
    Generate TTS audio using Microsoft Edge TTS (free).

    Randomly picks from the configured voices for variety.
    """
    tts_cfg = config["tts"]
    voice = random.choice(tts_cfg.get("voices", ["en-US-GuyNeural"]))
    rate = tts_cfg.get("speed", "+10%")
    pitch = tts_cfg.get("pitch", "+0Hz")

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _edge_tts_async(text, output_path, voice, rate, pitch)
        )
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# ElevenLabs (PAID UPGRADE)
# ---------------------------------------------------------------------------

def generate_tts_elevenlabs(
    text: str,
    output_path: Path,
    config: dict[str, Any],  # noqa: ARG001
) -> bool:
    """
    Generate TTS audio using ElevenLabs API (paid).

    Activate: set tts.provider = "elevenlabs" in config.yaml
    and add ELEVENLABS_API_KEY to GitHub Secrets.

    Docs: https://docs.elevenlabs.io/api-reference/text-to-speech
    """
    import requests

    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise EnvironmentError("ELEVENLABS_API_KEY not set.")

    voice_id = (
        config.get("tts", {}).get("elevenlabs", {}).get("voice_id")
        or "21m00Tcm4TlvDq8ikWAM"  # default: Rachel
    )
    model = (
        config.get("tts", {}).get("elevenlabs", {}).get("model")
        or "eleven_turbo_v2"
    )

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": model,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)
        log.info("✓ ElevenLabs TTS saved: %s", output_path)
        return True
    except requests.RequestException as exc:
        log.error("ElevenLabs TTS failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# OpenAI TTS (PAID UPGRADE — alternative)
# ---------------------------------------------------------------------------

def generate_tts_openai(
    text: str,
    output_path: Path,
    config: dict[str, Any],  # noqa: ARG001
) -> bool:
    """
    Generate TTS using OpenAI TTS API (paid, ~$15/1M chars).
    Activate: set tts.provider = "openai" in config.yaml.
    """
    try:
        from openai import OpenAI  # type: ignore[import]
    except ImportError:
        log.error("openai package not installed. Run: pip install openai")
        return False

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY not set.")

    client = OpenAI(api_key=api_key)
    try:
        response = client.audio.speech.create(
            model="tts-1",
            voice="onyx",
            input=text,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        response.stream_to_file(str(output_path))
        log.info("✓ OpenAI TTS saved: %s", output_path)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("OpenAI TTS failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

TTS_PROVIDERS: dict[str, Any] = {
    "edge-tts": generate_tts_edge,
    "elevenlabs": generate_tts_elevenlabs,
    "openai": generate_tts_openai,
}


def generate_tts(
    text: str,
    output_path: Path | str,
    config: dict[str, Any] | None = None,
) -> bool:
    """
    Generate TTS audio from `text` and save to `output_path`.

    Provider is read from config.yaml → tts.provider.
    Upgrade to paid TTS: change tts.provider + add API key secret.

    Args:
        text:        The spoken line to synthesize.
        output_path: Destination path (e.g. /tmp/audio.mp3).
        config:      Loaded config dict (loaded from file if None).

    Returns:
        True on success, False on failure.
    """
    if config is None:
        config = load_config()

    output_path = Path(output_path)
    provider = config["tts"].get("provider", "edge-tts")

    if provider not in TTS_PROVIDERS:
        raise ValueError(f"Unknown TTS provider: '{provider}'. Valid: {list(TTS_PROVIDERS)}")

    log.info("TTS provider: %s | Output: %s", provider, output_path)
    return TTS_PROVIDERS[provider](text, output_path, config)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate TTS audio from text")
    parser.add_argument("--text", required=True, help="Text to synthesize")
    parser.add_argument("--output", required=True, help="Output file path (.mp3)")
    parser.add_argument("--dry-run", action="store_true", help="Skip generation")
    args = parser.parse_args()

    if args.dry_run:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("DRY RUN")
        log.info("Dry-run: placeholder at %s", out)
    else:
        ok = generate_tts(args.text, args.output)
        raise SystemExit(0 if ok else 1)
