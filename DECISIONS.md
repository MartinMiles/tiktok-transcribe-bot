# TikTok Transcribe Telegram Bot — Architecture & Decision Log

## What This Bot Does

A Telegram bot that accepts TikTok video URLs, extracts the audio, transcribes it to text, and sends the transcript back as readable paragraphs. Users can either send a bare TikTok link or use `/transcribe <url>`.

## Architecture

```
User sends TikTok URL
        |
   bot.py (Telegram polling)
        |
   transcriber.py
        |
        +---> yt-dlp: downloads TikTok video, extracts audio as MP3
        |        (requires ffmpeg for audio conversion)
        |
        +---> Groq API: sends MP3 to Groq's whisper-large-v3 endpoint
        |        (returns raw transcript text)
        |
        +---> _format_transcript(): groups sentences into paragraphs
        |
   Bot sends formatted text back to user
```

## File Structure

| File | Purpose |
|---|---|
| `bot.py` | Telegram bot entry point. Handles `/start`, `/help`, `/transcribe`, and plain URL messages. Uses `python-telegram-bot` library with polling mode. |
| `transcriber.py` | Core logic. Downloads audio via yt-dlp Python API, sends to Groq Whisper API, formats result into paragraphs. |
| `Dockerfile` | Production container. Based on `python:3.13-slim` with ffmpeg installed via apt. |
| `requirements.txt` | Four lightweight dependencies: python-telegram-bot, yt-dlp, httpx, python-dotenv. |
| `.env.example` | Template for required environment variables. |
| `.gitignore` | Excludes `.env`, `__pycache__/`, `*.pyc`, `.venv/`. |

## Environment Variables

| Variable | Required | Where to get it |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Talk to @BotFather on Telegram |
| `GROQ_API_KEY` | Yes | https://console.groq.com -> API Keys |

## Key Decisions and Why

### 1. Groq API instead of local Whisper

**What we tried first:** OpenAI's open-source Whisper running locally via the `openai-whisper` Python package.

**Why it was abandoned — two independent blockers:**

- **Railway image size limit:** Whisper depends on PyTorch (~2 GB). The Docker image ballooned to 5.4 GB, exceeding Railway's free tier limit of 4 GB.
- **Windows Server RAM:** The target deployment server (Windows Server with IIS) has only 4 GB total RAM with ~764 MB free. Whisper's `base` model needs ~1 GB just to load, causing `RuntimeError: [enforce fail at alloc_cpu.cpp]` — an out-of-memory error during `torch.empty()` tensor allocation.

**What we use instead:** Groq's free Whisper API (`whisper-large-v3`). This is actually better — the `large-v3` model is far more accurate than the `base` model we would have run locally, the image is ~200 MB instead of 5.4 GB, and RAM usage is minimal. Free tier allows ~7,000 audio-seconds/day (~100 TikTok videos).

### 2. yt-dlp instead of getsubs.cc

**What we tried first:** The original plan was to use https://getsubs.cc as the transcription service.

**Why it was abandoned:** getsubs.cc has aggressive anti-bot protection:
- Cloudflare Turnstile CAPTCHA that must be solved before the AJAX call fires
- AES-256-CBC encrypted tokens (hardcoded password: `Oh5E3Zvxarh986Gdn4duhb664dX1PpX2`)
- Server-generated session tokens (`hash1`, `task_id`, `htoken`)
- The Turnstile challenge blocks ALL automated browsers — we tested:
  - Playwright headless Chromium: blocked
  - Playwright headed Chromium: blocked
  - System Chrome (headless and headed): blocked
  - Anti-detection patches (`navigator.webdriver` override, `--disable-blink-features=AutomationControlled`): still blocked
- The Turnstile widget loads its iframe and runs challenge flows but never produces a valid token in any automated context.

**What we use instead:** yt-dlp downloads TikTok video audio directly (no browser needed), then Groq transcribes it. This is more reliable, faster, and has no CAPTCHA dependency.

### 3. Dockerfile instead of nixpacks.toml / Procfile

**What we tried first:** Railway's default Nixpacks build system with a `nixpacks.toml` specifying `nixPkgs = ["ffmpeg"]`.

**Why it was abandoned:** Despite correct configuration (tried both `ffmpeg` and `ffmpeg-full` nix packages, plus `aptPkgs`), ffmpeg was not available at runtime. yt-dlp failed with `ffprobe and ffmpeg not found`.

**What we use instead:** A `Dockerfile` with explicit `apt-get install ffmpeg`. This gives us full control over the build and is the most reliable way to ensure system dependencies are present.

### 4. Polling mode instead of webhooks

The bot uses Telegram's polling mode (`app.run_polling()`), not webhooks. This means:
- The bot maintains a persistent outbound connection to Telegram's API
- No inbound ports need to be opened
- No SSL certificate or domain needed
- Works behind NAT, firewalls, and on platforms without a public URL
- Simpler to set up and debug

For a low-traffic bot like this, polling is the right choice. Webhooks would only matter at scale (hundreds of concurrent users).

### 5. httpx instead of requests

The bot is async (`python-telegram-bot` is async, yt-dlp runs in an executor). We use `httpx.AsyncClient` for the Groq API call to stay non-blocking. The `requests` library is synchronous and would block the event loop.

### 6. yt-dlp Python API instead of CLI subprocess

Initially we tried calling `yt-dlp` as a subprocess, but it wasn't on PATH in all environments. Using the Python API (`import yt_dlp; yt_dlp.YoutubeDL(opts).download([url])`) avoids PATH issues entirely. It runs in a thread executor since it's synchronous and blocking.

## Deployment: Railway

### Current setup
- **Platform:** Railway (https://railway.com)
- **Plan:** Trial / Hobby ($5/month includes $5 resource credit)
- **Repo:** https://github.com/MartinMiles/tiktok-transcribe-bot (public)
- **Build:** Dockerfile-based
- **Branch:** `master` (also mirrored to `main`)

### Known Railway quirks
- **Auto-deploy not connected:** As of initial setup, auto-deploy from GitHub pushes did not trigger. Manual pulls from the Railway dashboard are needed to deploy new commits. This may be fixable by disconnecting and reconnecting the GitHub repo in Railway settings.
- **Branch confusion:** The repo has both `master` and `main` branches. Both are kept in sync via `git push origin master:main`. Railway may be watching either — check Settings if deploys seem stale.
- **Free tier limits:** 4 GB Docker image size limit. This is why we can't use local Whisper/PyTorch.
- **Public repo required on free tier:** Private GitHub repos require the Railway GitHub App to be installed and granted access, or an upgraded plan.

### How to deploy updates
```bash
# Make changes locally, then:
git add <files>
git commit -m "description"
git push origin master
git push origin master:main

# Then in Railway dashboard: manually trigger a deploy
```

### Environment variables in Railway
Set via the Railway dashboard → Service → Variables tab. These are NOT in the repo (`.env` is gitignored).

## Alternative Deployment: Windows Server (NSSM)

This was explored for a Windows Server (4 GB RAM, IIS already running). The approach:
- Install Python 3.13, ffmpeg, create a venv
- Use NSSM (Non-Sucking Service Manager) to run `python bot.py` as a Windows Service
- Service survives logoff/reboot, auto-restarts on crash

**Why we didn't use it:** The server only has 764 MB free RAM — not enough for local Whisper. The Groq API version could work there, but Railway was chosen for simplicity.

If revisiting the Windows Server approach, the NSSM commands are:
```bat
nssm install TikTokTranscribeBot
# Path: C:\TelegramBots\tiktok-transcribe\venv\Scripts\python.exe
# Startup dir: C:\TelegramBots\tiktok-transcribe
# Arguments: bot.py
nssm start TikTokTranscribeBot
```

## Transcript Formatting

Raw Whisper output is a single block of text. `_format_transcript()` in `transcriber.py`:
1. Collapses multiple whitespace into single spaces
2. Splits on sentence boundaries (`[.!?]` followed by whitespace)
3. Groups every 4 sentences into a paragraph
4. Joins paragraphs with double newlines

This makes long transcripts readable in Telegram.

## URL Handling

The bot accepts TikTok URLs in these formats:
- `https://www.tiktok.com/@user/video/1234567890`
- `https://vm.tiktok.com/ZNRQGUqTF/` (short links)
- `https://vt.tiktok.com/...` (another short link variant)
- With or without trailing slash

The regex `https?://(?:www\.|vm\.|vt\.)?tiktok\.com/\S+` matches all of these. Users can either paste a URL directly or use `/transcribe <url>`.

## Limits and Constraints

| Constraint | Value | Source |
|---|---|---|
| Groq free tier | ~7,000 audio-seconds/day | Groq pricing |
| Telegram message limit | 4,096 characters | Telegram API |
| yt-dlp download timeout | 120 seconds | transcriber.py |
| Bot processing timeout | 90 seconds | bot.py |
| Railway image size (free) | 4 GB | Railway pricing |

## Potential Future Issues

- **yt-dlp breaking:** TikTok frequently changes their site. yt-dlp releases updates to keep up. If downloads start failing, update yt-dlp: bump the version in `requirements.txt` and redeploy.
- **Groq API changes:** If Groq changes pricing, rate limits, or deprecates `whisper-large-v3`, update the model name in `transcriber.py` line 18 (`GROQ_TRANSCRIBE_URL`) and the model parameter in `_transcribe_groq()`.
- **Telegram API changes:** `python-telegram-bot` is pinned to 21.10. Major version bumps may require code changes.
