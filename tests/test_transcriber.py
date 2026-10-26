"""Offline regressions: generated media, no TikTok/Telegram/Groq requests."""

import asyncio
import copy
import gc
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yt_dlp

import transcriber


class FormatSelectionTests(unittest.TestCase):
    def select(self, formats):
        with yt_dlp.YoutubeDL({"format": transcriber._AUDIO_FORMAT, "quiet": True}) as ydl:
            info = ydl.process_ie_result({
                "id": "fixture", "title": "fixture", "formats": formats,
            }, download=False)
        return info["format_id"]

    @staticmethod
    def fmt(name, video, audio="aac", height=540):
        return {
            "format_id": name, "url": f"https://fixture.invalid/{name}.mp4",
            "ext": "mp4", "vcodec": video, "acodec": audio, "height": height,
        }

    def test_prefers_video_audio_over_h265_and_separate_soundtrack(self):
        for codec in ("h264", "avc1.64001f"):
            with self.subTest(codec=codec):
                self.assertEqual(self.select([
                    self.fmt("soundtrack", "none"),
                    self.fmt("compatible", codec),
                    self.fmt("silent-high-quality", "h265", height=1080),
                ]), "compatible")

    def test_falls_back_when_h264_is_unavailable(self):
        self.assertEqual(self.select([self.fmt("hevc", "h265")]), "hevc")

    def test_audio_only_posts_still_work(self):
        self.assertEqual(self.select([self.fmt("audio", "none")]), "audio")

    def test_skips_explicitly_silent_h264(self):
        self.assertEqual(self.select([
            self.fmt("silent", "h264", "none"), self.fmt("audio", "none"),
        ]), "audio")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "ffmpeg and ffprobe are required for media regression tests")
class MediaRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        # yt-dlp's file:// response objects can retain handles in reference cycles.
        # Collect them before Windows removes the generated fixture directory.
        cls.addClassCleanup(gc.collect)
        cls.root = Path(cls.tmp.name)
        cls.voiced = cls.root / "voiced.mp4"
        cls.silent = cls.root / "silent.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=size=64x64:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "0.5", "-c:v", "libx264", "-c:a", "aac", str(cls.voiced),
        ], check=True, capture_output=True, timeout=30)
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(cls.voiced), "-an", "-c:v", "copy", str(cls.silent),
        ], check=True, capture_output=True, timeout=30)

    def run_download(self, formats, output, selector=None):
        def local_download(url, opts):
            # Replace only network extraction with a synthetic extractor result.
            opts = {**opts, "enable_file_urls": True}
            if selector:
                opts["format"] = selector
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.process_ie_result({
                    "id": "fixture", "title": "fixture",
                    "formats": copy.deepcopy(formats),
                })

        with patch.object(transcriber, "_run_ytdlp", side_effect=local_download):
            asyncio.run(transcriber._download_audio("offline-fixture", str(output), 30_000))

    def formats(self):
        return [
            {"format_id": "h264", "url": self.voiced.as_uri(), "ext": "mp4",
             "vcodec": "h264", "acodec": "aac", "height": 540},
            # Reproduce TikTok metadata claiming AAC on a file with no audio.
            {"format_id": "bytevc1", "url": self.silent.as_uri(), "ext": "mp4",
             "vcodec": "h265", "acodec": "aac", "height": 1080},
        ]

    def test_old_selector_reproduces_reported_ffprobe_error(self):
        with tempfile.TemporaryDirectory() as dest:
            with self.assertRaisesRegex(RuntimeError, "Audio could not be read") as caught:
                self.run_download(self.formats(), Path(dest) / "audio.mp3", "bestaudio/best")
            self.assertIn("unable to obtain file audio codec", str(caught.exception.__cause__))

    def test_new_selector_produces_real_mp3_with_audio(self):
        with tempfile.TemporaryDirectory() as dest:
            output = Path(dest) / "audio.mp3"
            self.run_download(self.formats(), output)
            result = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(output),
            ], check=True, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.stdout.strip(), "mp3")
            self.assertGreater(output.stat().st_size, 0)

    def test_no_audio_returns_actionable_error(self):
        with tempfile.TemporaryDirectory() as dest:
            with self.assertRaisesRegex(RuntimeError, "may have no audio track"):
                self.run_download(self.formats()[1:], Path(dest) / "audio.mp3")


class DownloadErrorTests(unittest.TestCase):
    def test_rejection_stops_without_retry_or_exposing_raw_error(self):
        with patch.object(transcriber, "_run_ytdlp", side_effect=yt_dlp.utils.DownloadError(
            "HTTP Error 403: private diagnostic details",
        )) as download:
            with self.assertRaisesRegex(RuntimeError, "^TikTok download or audio conversion failed.$"):
                asyncio.run(transcriber._download_audio("offline-fixture", "unused.mp3", 1000))
        self.assertEqual(download.call_count, 1)


if __name__ == "__main__":
    unittest.main()
