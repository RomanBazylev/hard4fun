# 🌀 GlitchRealityAI — Automated YouTube Shorts Channel

> **Fully automated, 100% free to start.** Generates and publishes 3–5 "Glitch in Reality" Shorts per day using AI video generation, Microsoft Edge TTS, and FFmpeg — all running on GitHub Actions (free tier).

[![Daily Pipeline](https://github.com/YOUR_USERNAME/glitch-reality-ai-youtube/actions/workflows/daily-shorts.yml/badge.svg)](https://github.com/YOUR_USERNAME/glitch-reality-ai-youtube/actions/workflows/daily-shorts.yml)

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [YouTube OAuth2 Setup (Step-by-Step)](#youtube-oauth2-setup)
3. [How It Works](#how-it-works)
4. [Repository Structure](#repository-structure)
5. [Configuration](#configuration)
6. [Dry Run / Testing](#dry-run--testing)
7. [Adding a New Video Model](#adding-a-new-video-model)
8. [Upgrading to Paid APIs](#upgrading-to-paid-apis)
9. [Troubleshooting](#troubleshooting)
10. [FAQ](#faq)

---

## Quick Start

### Prerequisites
- GitHub account (free)
- Google account (for YouTube channel)

### 1. Fork and clone

```bash
git clone https://github.com/YOUR_USERNAME/glitch-reality-ai-youtube.git
cd glitch-reality-ai-youtube
```

### 2. Set up YouTube OAuth2

See the [detailed guide below](#youtube-oauth2-setup). You'll get three values:
- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`

### 3. Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Required | Description |
|---|---|---|
| `YOUTUBE_CLIENT_ID` | ✅ | From Google Cloud Console |
| `YOUTUBE_CLIENT_SECRET` | ✅ | From Google Cloud Console |
| `YOUTUBE_REFRESH_TOKEN` | ✅ | From OAuth flow below |
| `GEMINI_API_KEY` | Optional | Free at [aistudio.google.com](https://aistudio.google.com) — for AI idea generation |
| `HF_TOKEN` | Optional | From [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) — increases rate limits |

### 4. Enable Actions

Go to **Actions** tab → click **Enable GitHub Actions**.

### 5. Test with a dry run

Go to **Actions → Daily Shorts Pipeline → Run workflow** → check **Dry run** → **Run workflow**.

Watch the logs. If everything looks green (no red ✗), you're ready to go live!

### 6. Go live

Push any commit or wait for midnight UTC. The pipeline will automatically generate and upload 4 Shorts.

---

## YouTube OAuth2 Setup

This is the most involved step. Follow these instructions carefully.

### Step 1 — Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click **Select a project → New Project**
3. Name: `GlitchRealityAI` → **Create**

### Step 2 — Enable YouTube Data API v3

1. In your project, go to **APIs & Services → Library**
2. Search **YouTube Data API v3** → **Enable**

### Step 3 — Create OAuth2 credentials

1. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. Application type: **Desktop app**
3. Name: `glitch-reality-local`
4. Click **Create**
5. Download the JSON — you'll see `client_id` and `client_secret`

### Step 4 — Configure consent screen

1. Go to **OAuth consent screen**
2. User type: **External** → **Create**
3. Fill in app name (`GlitchRealityAI`), your email, etc.
4. Scopes: click **Add or remove scopes** → search `youtube.upload` → add it
5. Test users: add your own Gmail address
6. **Save and continue** through all steps

### Step 5 — Get the refresh token (one-time)

Run this script **once on your local machine**:

```bash
pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client

python - <<'EOF'
from google_auth_oauthlib.flow import InstalledAppFlow

# Replace with your actual client_id and client_secret
CLIENT_ID = "YOUR_CLIENT_ID.apps.googleusercontent.com"
CLIENT_SECRET = "YOUR_CLIENT_SECRET"

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    },
    scopes=["https://www.googleapis.com/auth/youtube.upload"],
)
credentials = flow.run_local_server(port=0)
print("REFRESH TOKEN:", credentials.refresh_token)
EOF
```

A browser window will open. Log in with the YouTube channel account and grant permission. Copy the printed refresh token.

### Step 6 — Add secrets to GitHub

```
YOUTUBE_CLIENT_ID     = <from Step 3>
YOUTUBE_CLIENT_SECRET = <from Step 3>
YOUTUBE_REFRESH_TOKEN = <from Step 5>
```

---

## How It Works

```
Daily cron (midnight UTC)
         │
         ▼
  Select 4 prompts from ideas.json
  (randomised + style-injected)
         │
    ┌────┴────┐
    │  Video  │  Hugging Face Gradio Space API
    │  gen    │  (Wan2.1 → HunyuanVideo → CogVideoX)
    └────┬────┘
         │
    ┌────┴────┐
    │   TTS   │  edge-tts (Microsoft Edge, en-US-GuyNeural)
    └────┬────┘
         │
    ┌────┴────┐
    │ Montage │  FFmpeg: scale → glitch effects → subtitles → music mix
    └────┬────┘
         │
    ┌────┴────┐
    │ Thumb   │  FFmpeg: extract frame → overlay bold text
    └────┬────┘
         │
    ┌────┴────┐
    │ Upload  │  YouTube Data API v3 (OAuth2 refresh token)
    └─────────┘
```

### Anti-duplicate strategy

To avoid YouTube's "reused AI content" flags, each video is unique through:

- **Prompt randomisation** — same base idea gets random style suffixes + scene details injected
- **Voice variety** — rotates between 4 Edge TTS voices
- **Seed randomisation** — different random seed per generation call
- **Model rotation** — tries different Spaces (Wan → HunyuanVideo → CogVideoX)
- **Used-prompt tracking** — `logs/used_prompts.json` prevents exact ID reuse for 100 videos

---

## Repository Structure

```
glitch-reality-ai-youtube/
├── pipeline.py              # Master orchestrator — start here
├── prompts/
│   ├── ideas.json           # Prompt database (20+ base ideas, AI-expandable)
│   └── daily_prompts.py     # Selector + Gemini-powered idea generator
├── scripts/
│   ├── generate_video.py    # HF Gradio + Kling/Runway upgrade stubs
│   ├── tts.py               # edge-tts + ElevenLabs/OpenAI upgrade stubs
│   ├── montage.py           # FFmpeg pipeline (glitch fx + subtitles + music)
│   ├── thumbnail.py         # Frame extraction + text overlay
│   └── upload.py            # YouTube API v3 upload + SEO generation
├── config/
│   ├── config.yaml          # All settings (edit this to customise)
│   └── secrets.example.yaml # Template — never commit secrets.yaml
├── assets/
│   ├── music/               # Put .mp3 background tracks here
│   └── fonts/               # Impact.ttf (auto-downloaded by workflow)
├── .github/workflows/
│   └── daily-shorts.yml     # CI/CD pipeline
├── logs/
│   ├── .gitkeep
│   └── used_prompts.json    # Tracked to avoid prompt reuse
├── requirements.txt
└── README.md
```

---

## Configuration

All settings live in `config/config.yaml`. Key things to tweak:

```yaml
channel:
  daily_videos: 4        # Change to 3–5

video:
  provider: "huggingface"  # Free tier
  resolution: "576x1024"
  fps: 16

tts:
  provider: "edge-tts"   # Free tier
  voices:
    - "en-US-GuyNeural"  # Add/remove voices
```

### Adding background music

1. Download royalty-free tracks from [YouTube Audio Library](https://www.youtube.com/audiolibrary) or [Pixabay Music](https://pixabay.com/music/)
2. Place `.mp3` files in `assets/music/`
3. Commit them — the pipeline picks one randomly per video
4. Adjust volume: `montage.music_volume: 0.15` (0 = off, 1 = full volume)

---

## Dry Run / Testing

Test without uploading to YouTube or calling paid APIs:

```bash
# Local test
python pipeline.py --dry-run --count 1

# In GitHub Actions: use workflow_dispatch → check "Dry run"
```

In dry-run mode:
- Video generation, TTS, and montage **are** attempted (tests the full chain)
- YouTube upload is **skipped**
- Placeholder files are created if any step fails

To test just one script:
```bash
# Test TTS only
python scripts/tts.py --text "The simulation broke." --output /tmp/test.mp3

# Test prompt selection
python prompts/daily_prompts.py --count 2 --dry-run --output text

# Test montage (needs ffmpeg)
python scripts/montage.py \
  --video /path/to/video.mp4 \
  --audio /path/to/audio.mp3 \
  --text "Wait, that's not right." \
  --output /tmp/out.mp4
```

---

## Adding a New Video Model

1. Open `config/config.yaml` → add to `video.huggingface.spaces`:

```yaml
spaces:
  - url: "https://your-new-space.hf.space"
    api_path: "/run/predict"
    timeout: 300
```

2. The pipeline automatically falls back to the next Space if one fails.

3. For a fundamentally different provider (e.g. a new paid API), add a function in `scripts/generate_video.py`:

```python
def generate_video_myprovider(prompt, config, output_path):
    # ... your implementation ...
    return True  # or False on failure

PROVIDERS["myprovider"] = generate_video_myprovider
```

Then set `video.provider: "myprovider"` in `config.yaml`.

---

## Upgrading to Paid APIs

The entire architecture is built for drop-in upgrades. Each upgrade takes ~5 minutes.

### Upgrade TTS to ElevenLabs

**Cost:** ~$5/month (starter plan, plenty for 120+ videos/month)

1. Sign up at [elevenlabs.io](https://elevenlabs.io) → get API key
2. Add secret: `ELEVENLABS_API_KEY = your_key`
3. Get your voice ID from the ElevenLabs dashboard
4. Edit `config/config.yaml`:

```yaml
tts:
  provider: "elevenlabs"
  elevenlabs:
    voice_id: "YOUR_VOICE_ID"
    model: "eleven_turbo_v2"
```

5. Commit and push. Done.

### Upgrade video to Kling API

**Cost:** ~$0.14/video (Kling v1.5, 5s clip)

1. Sign up at [klingai.com](https://klingai.com) → get API key
2. Add secret: `KLING_API_KEY = your_key`
3. Edit `config/config.yaml`:

```yaml
video:
  provider: "kling"
```

4. Commit and push. Done.

### Upgrade video to Runway Gen-3

**Cost:** ~$0.05/second of video

1. Sign up at [runwayml.com](https://runwayml.com) → get API key
2. Add secret: `RUNWAY_API_KEY = your_key`
3. Edit `config/config.yaml`:

```yaml
video:
  provider: "runway"
```

4. Commit and push. Done.

---

## Troubleshooting

### "All HuggingFace Spaces exhausted"

HF free Spaces can be slow or offline. Fixes:
- Add `HF_TOKEN` secret (increases rate limits)
- Add more Spaces to `config.yaml` → `video.huggingface.spaces`
- HF Spaces are often offline during peak hours (17:00–22:00 UTC) — the 14:00 UTC cron run is timed to avoid this

### "YouTube upload failed"

1. Check your refresh token hasn't expired (they last ~6 months of inactivity — just re-run the OAuth script)
2. Verify your OAuth consent screen includes `youtube.upload` scope
3. Make sure the YouTube channel is not restricted / brand new accounts may have upload limits

### "Audio mix failed"

Usually means a music file in `assets/music/` is corrupted. Delete it and try a fresh download.

### "Subtitle burn failed"

The font file is missing. The workflow auto-downloads a fallback, but you can also manually add `Impact.ttf` to `assets/fonts/`.

---

## FAQ

**Q: Will YouTube detect this as AI content?**
Each video is unique due to random prompt enrichment, voice rotation, and model switching. Always review YouTube's policies on AI-generated content disclosures.

**Q: How much does it cost to run?**
Zero dollars on the free tier. GitHub Actions free tier gives 2,000 minutes/month. Each run takes ~30–60 minutes = fits within free tier for daily runs.

**Q: How do I add more base prompt ideas?**
Edit `prompts/ideas.json` directly, or trigger `--refresh-ideas` in the workflow to auto-generate new ideas via Gemini (free tier).

**Q: Can I change the niche?**
Yes — just edit `prompts/ideas.json` with your new ideas and update the style suffixes. The entire architecture is niche-agnostic.

**Q: The video is portrait but my test plays in landscape?**
The pipeline forces 1080×1920 (9:16). Make sure your media player handles vertical video. YouTube Shorts requires the `#shorts` tag and portrait orientation — both are handled automatically.
