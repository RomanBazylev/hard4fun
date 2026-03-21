"""
pipeline.py
===========
Master orchestrator for GlitchRealityAI.

Runs the full daily pipeline:
  1. Select N prompts for today
  2. For each prompt:
      a. Generate video (HF Spaces)
      b. Generate TTS audio
      c. Assemble Short (ffmpeg glitch + subtitles + music)
      d. Generate thumbnail
      e. Upload to YouTube
  3. Log results + report failures to GitHub Issues

Usage:
    # Normal run (produces 4 videos)
    python pipeline.py

    # Dry-run (no actual API calls, creates placeholder files)
    python pipeline.py --dry-run

    # Run with specific number of videos
    python pipeline.py --count 3

    # Generate new AI ideas before running
    python pipeline.py --refresh-ideas
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config" / "config.yaml"
LOGS_DIR = ROOT / "logs"

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("pipeline")


def load_config() -> dict[str, Any]:
    with CONFIG_FILE.open() as f:
        return yaml.safe_load(f)


def setup_log_file() -> Path:
    """Create a date-stamped log file in logs/."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"run_{today}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    )
    logging.getLogger().addHandler(file_handler)
    return log_file


def report_failure_to_github(title: str, body: str) -> None:
    """
    Open a GitHub Issue for pipeline failures.
    Requires GITHUB_TOKEN and GITHUB_REPO environment variables.
    Only runs if github_issue_on_failure is True in config.
    """
    config = load_config()
    if not config.get("notifications", {}).get("github_issue_on_failure", False):
        return

    token = os.getenv("GITHUB_TOKEN", "")
    repo = os.getenv("GITHUB_REPO", "")
    if not token or not repo:
        log.warning("GITHUB_TOKEN or GITHUB_REPO not set — skipping issue creation.")
        return

    import requests  # noqa: PLC0415

    label = config["notifications"].get("github_issue_label", "auto-error")
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": title, "body": body, "labels": [label]},
        timeout=30,
    )
    if resp.status_code == 201:
        log.info("GitHub Issue created: %s", resp.json().get("html_url"))
    else:
        log.warning("Failed to create GitHub Issue: %d %s", resp.status_code, resp.text[:200])


# ---------------------------------------------------------------------------
# Per-video pipeline
# ---------------------------------------------------------------------------

def process_one_video(
    prompt: dict[str, Any],
    work_dir: Path,
    config: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Run the full pipeline for a single video prompt.

    Returns a result dict with keys:
      - id, hook, status, video_id, error
    """
    from scripts.generate_video import generate_video
    from scripts.montage import assemble_short
    from scripts.thumbnail import generate_thumbnail
    from scripts.tts import generate_tts
    from scripts.upload import upload_short

    pid = prompt["id"]
    hook = prompt["hook"]
    visual_prompt = prompt["visual_prompt"]
    voice_line = prompt["voice_line"]

    result: dict[str, Any] = {"id": pid, "hook": hook, "status": "pending", "video_id": None, "error": None}

    raw_video = work_dir / f"{pid}_raw.mp4"
    audio_file = work_dir / f"{pid}_voice.mp3"
    final_video = work_dir / f"{pid}_final.mp4"
    thumb_file = work_dir / f"{pid}_thumb.jpg"

    log.info("=" * 60)
    log.info("Processing: %s — %s", pid, hook)
    log.info("Visual: %s", visual_prompt[:100])

    # ------------------------------------------------------------------
    # DRY-RUN: skip all external API calls, create placeholder files
    # ------------------------------------------------------------------
    if dry_run:
        log.info("DRY RUN — creating placeholder files, skipping API calls.")
        for p in (raw_video, audio_file, final_video):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"DRY RUN placeholder: {p.name}")
        thumb_file.parent.mkdir(parents=True, exist_ok=True)
        thumb_file.write_bytes(b"DRY RUN placeholder thumbnail")
        result["status"] = "success"
        result["video_id"] = "DRY_RUN"
        log.info("✓ Dry-run complete for %s", pid)
        return result

    try:
        # Step 1: Generate video
        log.info("[1/5] Generating video …")
        if not generate_video(visual_prompt, raw_video, config=config):
            raise RuntimeError("Video generation failed after all retries.")

        # Step 2: TTS audio
        log.info("[2/5] Generating TTS audio …")
        if not generate_tts(voice_line, audio_file, config=config):
            raise RuntimeError("TTS generation failed.")

        # Step 3: Assemble Short
        log.info("[3/5] Assembling Short …")
        if not assemble_short(raw_video, audio_file, voice_line, final_video, config=config):
            raise RuntimeError("Montage assembly failed.")

        # Step 4: Thumbnail
        log.info("[4/5] Generating thumbnail …")
        generate_thumbnail(final_video, hook, thumb_file, config=config)  # non-fatal

        # Step 5: Upload
        log.info("[5/5] Uploading to YouTube …")
        video_id = upload_short(
            video_path=final_video,
            thumbnail_path=thumb_file if thumb_file.exists() else None,
            hook=hook,
            config=config,
        )
        if not video_id:
            raise RuntimeError("YouTube upload failed after all retries.")
        result["video_id"] = video_id
        result["url"] = f"https://youtu.be/{video_id}"

        result["status"] = "success"
        log.info("✓ Done: %s → https://youtu.be/%s", pid, result.get("video_id", "N/A"))

    except Exception as exc:  # noqa: BLE001
        log.exception("Failed processing %s: %s", pid, exc)
        result["status"] = "failed"
        result["error"] = str(exc)

    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(count: int = 4, dry_run: bool = False, refresh_ideas: bool = False) -> None:
    """Main daily pipeline entry point."""
    log.info("🚀 GlitchRealityAI Pipeline starting (count=%d, dry_run=%s)", count, dry_run)
    start_time = time.time()

    log_file = setup_log_file()
    config = load_config()

    # Optionally refresh AI ideas
    if refresh_ideas:
        log.info("Refreshing AI ideas via Gemini …")
        from prompts.daily_prompts import generate_ai_ideas, save_ai_ideas

        new_ideas = generate_ai_ideas(count=config["prompts"]["daily_count"])
        if not dry_run:
            save_ai_ideas(new_ideas)

    # Select today's prompts
    from prompts.daily_prompts import select_daily_prompts

    prompts = select_daily_prompts(count=count)
    log.info("Selected %d prompts for today.", len(prompts))

    results: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="glitch_pipeline_") as tmp_dir:
        work_dir = Path(tmp_dir)
        for i, prompt in enumerate(prompts, 1):
            log.info("\n[Video %d/%d]", i, len(prompts))
            result = process_one_video(prompt, work_dir, config, dry_run=dry_run)
            results.append(result)

            # Small sleep between videos to avoid rate limits
            if i < len(prompts):
                time.sleep(15)

    # Summary
    elapsed = time.time() - start_time
    successes = [r for r in results if r["status"] == "success"]
    failures = [r for r in results if r["status"] == "failed"]

    log.info("\n%s", "=" * 60)
    log.info("PIPELINE COMPLETE in %.0fs", elapsed)
    log.info("✓ Succeeded: %d / %d", len(successes), len(results))
    log.info("✗ Failed:    %d / %d", len(failures), len(results))

    for r in successes:
        log.info("  ✓ %s → %s", r["id"], r.get("url", "N/A"))
    for r in failures:
        log.error("  ✗ %s → %s", r["id"], r.get("error", "unknown error"))

    # Save results log
    results_file = LOGS_DIR / f"results_{datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}.json"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with results_file.open("w") as f:
        json.dump(
            {"run_at": datetime.now(tz=timezone.utc).isoformat(), "results": results},
            f, indent=2, ensure_ascii=False,
        )
    log.info("Results saved: %s", results_file)

    # Report failures to GitHub
    if failures:
        error_body = "\n".join(
            f"- **{r['id']}** (`{r['hook']}`): {r['error']}" for r in failures
        )
        report_failure_to_github(
            title=f"Pipeline failures on {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')} ({len(failures)} failed)",
            body=f"## Pipeline run: {len(failures)}/{len(results)} failed\n\n{error_body}\n\nCheck GitHub Actions logs for details.",
        )

    if failures and not successes:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GlitchRealityAI daily pipeline")
    parser.add_argument("--count", type=int, default=None, help="Number of videos to produce")
    parser.add_argument("--dry-run", action="store_true", help="Skip actual API calls")
    parser.add_argument("--refresh-ideas", action="store_true", help="Generate new ideas via AI before running")
    args = parser.parse_args()

    cfg = load_config()
    count = args.count or cfg["channel"]["daily_videos"]
    run_pipeline(count=count, dry_run=args.dry_run, refresh_ideas=args.refresh_ideas)
