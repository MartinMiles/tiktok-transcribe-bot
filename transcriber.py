"""
TikTok video transcription using yt-dlp (audio download) + Groq Whisper API (speech-to-text).
"""

import asyncio
import logging
import os
import re
import tempfile

import httpx

logger = logging.getLogger(__name__)

_SENTENCES_PER_PARAGRAPH = 4

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


async def get_transcript(tiktok_url: str, timeout_ms: int = 120_000) -> str:
    """
    Download audio from *tiktok_url* via yt-dlp, transcribe via Groq Whisper API,
    and return cleaned paragraph text.

    Raises ``RuntimeError`` on any failure.
    """
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set in environment variables")

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.mp3")

        # 1. Download audio with yt-dlp
        logger.info("Downloading audio from %s", tiktok_url)
        await _download_audio(tiktok_url, audio_path, timeout_ms)

        if not os.path.exists(audio_path):
            raise RuntimeError("yt-dlp did not produce an audio file")

        # 2. Transcribe via Groq API
        logger.info("Sending to Groq Whisper API...")
        raw_text = await _transcribe_groq(audio_path, timeout_ms)

    if not raw_text:
        raise RuntimeError("Groq returned empty transcription")

    logger.info("Transcription complete (%d chars)", len(raw_text))
    return _format_transcript(raw_text)


async def _download_audio(url: str, output_path: str, timeout_ms: int) -> None:
    """Use yt-dlp Python API to extract audio as mp3."""
    import yt_dlp

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_path.replace(".mp3", ".%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "5",
        }],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }

    loop = asyncio.get_event_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(None, _run_ytdlp, url, ydl_opts),
            timeout=timeout_ms / 1000,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"yt-dlp timed out after {timeout_ms / 1000:.0f}s")


def _run_ytdlp(url: str, opts: dict) -> None:
    import yt_dlp
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


async def _transcribe_groq(audio_path: str, timeout_ms: int) -> str:
    """Send audio to Groq's Whisper API and return the transcript text."""
    async with httpx.AsyncClient() as client:
        with open(audio_path, "rb") as f:
            resp = await client.post(
                GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": ("audio.mp3", f, "audio/mpeg")},
                data={
                    "model": "whisper-large-v3",
                    "response_format": "text",
                },
                timeout=timeout_ms / 1000,
            )

    if resp.status_code != 200:
        raise RuntimeError(f"Groq API error {resp.status_code}: {resp.text[:300]}")

    return resp.text.strip()


def _format_transcript(raw: str) -> str:
    """Turn raw transcript text into readable paragraphs."""
    text = re.sub(r"\s{2,}", " ", raw).strip()

    if not text:
        return "(empty transcript)"

    sentences = re.split(r"(?<=[.!?])\s+", text)
    paragraphs: list[str] = []
    for i in range(0, len(sentences), _SENTENCES_PER_PARAGRAPH):
        chunk = " ".join(sentences[i : i + _SENTENCES_PER_PARAGRAPH])
        paragraphs.append(chunk)

    return "\n\n".join(paragraphs)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    url = sys.argv[1] if len(sys.argv) > 1 else "https://vm.tiktok.com/ZNRQGUqTF/"
    result = asyncio.run(get_transcript(url))
    print("\n=== TRANSCRIPT ===\n")
    print(result)
