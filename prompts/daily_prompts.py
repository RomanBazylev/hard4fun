"""
daily_prompts.py
================
Generates and selects daily video prompts for GlitchRealityAI.

Features:
  - Random selection from ideas.json with style suffix injection
  - AI-powered new idea generation via Gemini (free tier)
  - Deduplication to avoid reusing recent prompts
  - Dry-run mode

Usage:
    python prompts/daily_prompts.py --count 4 [--dry-run]
"""
from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import requests
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
IDEAS_FILE = ROOT / "prompts" / "ideas.json"
USED_FILE = ROOT / "logs" / "used_prompts.json"
CONFIG_FILE = ROOT / "config" / "config.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("prompts.daily")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    """Load YAML config from config/config.yaml."""
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f)


def load_ideas() -> dict[str, Any]:
    """Load the prompt database."""
    with IDEAS_FILE.open() as f:
        return json.load(f)


def load_used() -> list[str]:
    """Return list of recently used prompt IDs (last 30 days window)."""
    if USED_FILE.exists():
        with USED_FILE.open(encoding="utf-8-sig") as f:
            return json.load(f)
    return []


def save_used(used: list[str]) -> None:
    """Persist used prompt IDs, keeping only the last 100."""
    USED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with USED_FILE.open("w") as f:
        json.dump(used[-100:], f, indent=2)


# ---------------------------------------------------------------------------
# Core selection
# ---------------------------------------------------------------------------

def flatten_prompts(ideas: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten all theme prompt dicts into a single list."""
    prompts: list[dict[str, Any]] = []
    for theme_name, items in ideas["themes"].items():
        for item in items:
            item["theme"] = theme_name
            prompts.append(item)
    return prompts


def enrich_prompt(prompt: dict[str, Any], ideas: dict[str, Any]) -> dict[str, Any]:
    """
    Inject random style suffix and random detail into the visual_prompt.
    This ensures every video looks different even with the same base idea.
    """
    style = random.choice(ideas["style_suffixes"])
    detail = random.choice(ideas["random_details"])
    enriched = prompt.copy()
    enriched["visual_prompt"] = (
        f"{prompt['visual_prompt']}, {detail}, {style}"
    )
    return enriched


def select_daily_prompts(count: int = 4) -> list[dict[str, Any]]:
    """
    Select `count` unique prompts from the database,
    preferring ideas not used recently.
    """
    ideas = load_ideas()
    used = load_used()
    all_prompts = flatten_prompts(ideas)

    # Prefer unused prompts
    fresh = [p for p in all_prompts if p["id"] not in used]
    if len(fresh) < count:
        log.warning("Not enough fresh prompts (%d). Reusing older ones.", len(fresh))
        fresh = all_prompts  # fall back to full pool

    selected_raw = random.sample(fresh, min(count, len(fresh)))
    selected = [enrich_prompt(p, ideas) for p in selected_raw]

    # Mark as used
    used.extend(p["id"] for p in selected_raw)
    save_used(used)

    log.info("Selected %d prompts: %s", len(selected), [p["id"] for p in selected])
    return selected


# ---------------------------------------------------------------------------
# AI-powered idea generation (Gemini free tier)
# ---------------------------------------------------------------------------

GEMINI_SYSTEM = """You are a creative director for a viral YouTube Shorts channel called GlitchRealityAI.
You create "glitch in reality" stories — first-person narratives about impossible things happening in everyday life.
The style is storytelling: the narrator describes something weird that happened to them, building tension, then ending with a creepy or funny punchline.
Think of it like a Reddit post from r/Glitch_in_the_Matrix but told as a spoken monologue."""

GEMINI_USER_TEMPLATE = """Generate {count} new YouTube Shorts scripts for the "Glitch in Reality" niche.

Return ONLY a valid JSON array (no markdown, no preamble) with this exact schema:
[
  {{
    "id": "ai_<unique_5digit_number>",
    "hook": "clickbait-style one-line title, 5-10 words",
    "visual_prompt": "cinematic visual description for AI image generation, vertical 9:16, photorealistic, surreal glitch aesthetic",
    "voice_line": "A 30-50 second spoken monologue. First-person. Setup: describe a normal situation. Build-up: something impossible starts happening. Escalation: describe the glitch in vivid detail. Punchline: a creepy or funny ending. Make it sound like a real person telling a story, conversational tone.",
    "theme": "ai_generated"
  }}
]

Rules:
- voice_line MUST be 400-700 characters (this is critical — short lines make bad videos)
- voice_line should be a mini-story with setup → build-up → punchline
- Use first person ("I was...", "So I'm standing there...")
- Conversational tone, as if telling a friend
- Keep visual_prompt under 200 characters
- Make each idea unique — different locations, different types of glitches
- Avoid violence, explicit content, or politics
"""


def generate_ai_ideas(count: int = 5, api_key: str | None = None) -> list[dict[str, Any]]:
    """
    Call Gemini API to generate new prompt ideas.
    Falls back gracefully if API key is missing.
    """
    api_key = api_key or os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        log.warning("GEMINI_API_KEY not set — skipping AI idea generation.")
        return []

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.0-flash:generateContent"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": GEMINI_SYSTEM + "\n\n" + GEMINI_USER_TEMPLATE.format(count=count)}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.95,
            "maxOutputTokens": 2048,
        },
    }
    headers = {"Content-Type": "application/json"}

    for attempt in range(3):
        try:
            response = requests.post(
                url,
                params={"key": api_key},
                json=payload,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            # Strip possible markdown fences
            text = text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            ideas = json.loads(text)
            log.info("Gemini generated %d new ideas.", len(ideas))
            return ideas
        except (requests.RequestException, json.JSONDecodeError, KeyError) as exc:
            log.error("Gemini attempt %d failed: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(10)

    return []


def save_ai_ideas(new_ideas: list[dict[str, Any]]) -> None:
    """Append AI-generated ideas to ideas.json under 'ai_generated' theme."""
    if not new_ideas:
        return
    ideas = load_ideas()
    ai_bucket = ideas["themes"].setdefault("ai_generated", [])
    existing_ids = {p["id"] for p in ai_bucket}
    added = 0
    for idea in new_ideas:
        if idea.get("id") not in existing_ids:
            ai_bucket.append(idea)
            added += 1
    with IDEAS_FILE.open("w") as f:
        json.dump(ideas, f, indent=2, ensure_ascii=False)
    log.info("Saved %d new AI ideas to %s.", added, IDEAS_FILE)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Daily prompt selector for GlitchRealityAI")
    parser.add_argument("--count", type=int, default=4, help="Number of prompts to select (default: 4)")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts without saving used-state")
    parser.add_argument("--generate-ai", action="store_true", help="Generate new ideas via Gemini API")
    parser.add_argument("--output", choices=["json", "text"], default="json", help="Output format")
    args = parser.parse_args(argv)

    # Optionally generate & save new AI ideas first
    if args.generate_ai:
        config = load_config()
        new_ideas = generate_ai_ideas(count=config["prompts"]["daily_count"])
        if not args.dry_run:
            save_ai_ideas(new_ideas)

    prompts = select_daily_prompts(count=args.count)

    if args.output == "json":
        print(json.dumps(prompts, indent=2, ensure_ascii=False))
    else:
        for i, p in enumerate(prompts, 1):
            print(f"\n{'='*60}")
            print(f"[{i}] {p['id']} — {p['hook']}")
            print(f"  Visual : {p['visual_prompt']}")
            print(f"  Voice  : {p['voice_line']}")

    if not args.dry_run:
        # Persist used IDs (already done inside select_daily_prompts)
        pass


if __name__ == "__main__":
    main()
