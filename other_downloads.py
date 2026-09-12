import re
import logging
from pathlib import Path

import yt_dlp

import config

logger = logging.getLogger(__name__)

YOUTUBE_RE = re.compile(r"(youtube\.com/(watch\?v=|shorts/)|youtu\.be/)")
PINTEREST_RE = re.compile(r"(pinterest\.[a-z.]+/pin/|pin\.it/)")


def classify_other(url: str) -> str | None:
    if YOUTUBE_RE.search(url):
        return "youtube"
    if PINTEREST_RE.search(url):
        return "pinterest"
    return None


def download_with_ytdlp(url: str) -> list[str]:
    """Works for YouTube, Pinterest, and many other sites yt-dlp supports."""
    outtmpl = str(Path(config.DOWNLOAD_DIR) / "%(id)s.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bestvideo[filesize<50M]+bestaudio/best[filesize<50M]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        # merge_output_format may change the extension after download
        p = Path(filename)
        if not p.exists():
            p = p.with_suffix(".mp4")
        return [str(p)] if p.exists() else []
