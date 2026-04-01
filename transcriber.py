"""
TikTok video transcription using yt-dlp (audio download) + OpenAI Whisper (speech-to-text).
"""

import asyncio
import logging
import os
import re
import tempfile

import whisper

logger = logging.getLogger(__name__)

_SENTENCES_PER_PARAGRAPH = 4

# Whisper model: "base" is ~140 MB, good balance of speed and accuracy.
# Options: tiny, base, small, medium, large
_WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

_model = None


def _get_model():
    """Lazy-load the Whisper model (downloads on first use)."""
    global _model
    if _model is None:
        logger.info("Loading Whisper '%s' model...", _WHISPER_MODEL)
        _model = whisper.load_model(_WHISPER_MODEL)
        logger.info("Whisper model loaded.")
    return _model


async def get_transcript(tiktok_url: str, timeout_ms: int = 120_000) -> str:
    """
    Download audio from *tiktok_url* via yt-dlp, transcribe with Whisper,
    and return cleaned paragraph text.

    Raises ``RuntimeError`` on any failure.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.mp3")

        # 1. Download audio with yt-dlp
        logger.info("Downloading audio from %s", tiktok_url)
        await _download_audio(tiktok_url, audio_path, timeout_ms)

        if not os.path.exists(audio_path):
            raise RuntimeError("yt-dlp did not produce an audio file")

        # 2. Transcribe with Whisper (CPU-bound, run in thread)
        logger.info("Transcribing with Whisper...")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _transcribe, audio_path)

    raw_text = result["text"].strip()
    if not raw_text:
        raise RuntimeError("Whisper returned empty transcription")

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


def _transcribe(audio_path: str) -> dict:
    """Run Whisper inference on an audio file."""
    model = _get_model()
    return model.transcribe(audio_path)


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
