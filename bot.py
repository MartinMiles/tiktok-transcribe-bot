"""
Telegram bot that transcribes TikTok videos using yt-dlp + Whisper.

Usage:
    1. Copy .env.example -> .env and fill in your TELEGRAM_BOT_TOKEN.
    2. pip install -r requirements.txt
    3. python bot.py
"""

import logging
import os
import re

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from transcriber import get_transcript

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TIKTOK_URL_RE = re.compile(
    r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/\S+"
)

MAX_TELEGRAM_MESSAGE = 4096  # Telegram's per-message character limit


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi! Send me a TikTok link (or use /transcribe <url>) "
        "and I'll return the video's transcript."
    )


async def transcribe_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /transcribe <url>."""
    if not ctx.args:
        await update.message.reply_text("Usage: /transcribe <tiktok_url>")
        return
    url = ctx.args[0]
    await _process_url(update, url)


async def plain_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a bare TikTok URL sent as a message."""
    match = TIKTOK_URL_RE.search(update.message.text)
    if match:
        await _process_url(update, match.group(0))


async def _process_url(update: Update, url: str) -> None:
    if not TIKTOK_URL_RE.match(url):
        await update.message.reply_text("That doesn't look like a TikTok URL.")
        return

    status = await update.message.reply_text("Working on it — this may take up to a minute...")

    try:
        transcript = await get_transcript(url, timeout_ms=90_000)
    except RuntimeError as exc:
        logger.error("Transcription failed for %s: %s", url, exc)
        await status.edit_text(f"Sorry, transcription failed: {exc}")
        return
    except Exception as exc:
        logger.exception("Unexpected error for %s", url)
        await status.edit_text(f"Unexpected error: {type(exc).__name__}: {exc}")
        return

    # Send transcript, splitting if it exceeds Telegram's limit
    await status.delete()
    for chunk in _split_text(transcript, MAX_TELEGRAM_MESSAGE):
        await update.message.reply_text(chunk)


def _split_text(text: str, limit: int) -> list[str]:
    """Split *text* into chunks of at most *limit* characters on paragraph boundaries."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        addition = para if not current else "\n\n" + para
        if len(current) + len(addition) > limit:
            if current:
                chunks.append(current)
            current = para[: limit]  # hard-truncate a single giant paragraph
        else:
            current += addition
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("transcribe", transcribe_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_url))

    logger.info("Bot started — polling for updates")
    app.run_polling()


if __name__ == "__main__":
    main()
