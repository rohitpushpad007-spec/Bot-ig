import os
import re
import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)

YOUTUBE_RE = re.compile(r"(youtube\.com/(watch\?v=|shorts/)|youtu\.be/)")
PINTEREST_RE = re.compile(r"(pinterest\.[a-z.]+/pin/|pin\.it/)")

_cookie_file_path = str(Path(config.DOWNLOAD_DIR) / "yt_cookies.txt")
_cookie_file_written = False


def classify_other(url: str) -> str | None:
    if YOUTUBE_RE.search(url):
        return "youtube"
    if PINTEREST_RE.search(url):
        return "pinterest"
    return None


def _base_ydl_opts() -> dict:
    global _cookie_file_written
    opts = {
        "outtmpl": str(Path(config.DOWNLOAD_DIR) / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    # Optional: if the user has provided their own exported YouTube cookies
    # (Netscape cookies.txt format) via the YOUTUBE_COOKIES env var, use them.
    # This is needed because YouTube blocks plain server/datacenter requests.
    cookies_content = getattr(config, "YOUTUBE_COOKIES", "")
    if cookies_content:
        if not _cookie_file_written:
            Path(_cookie_file_path).write_text(cookies_content)
            _cookie_file_written = True
        opts["cookiefile"] = _cookie_file_path

    return opts


def download_with_ytdlp(url: str) -> list[str]:
    """Used for Pinterest — tries video first, falls back to image."""
    import yt_dlp

    video_opts = _base_ydl_opts()
    video_opts["format"] = "bestvideo[filesize<50M]+bestaudio/best[filesize<50M]/best"
    video_opts["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(video_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            p = Path(filename)
            if not p.exists():
                p = p.with_suffix(".mp4")
            if p.exists():
                return [str(p)]
    except yt_dlp.utils.DownloadError as e:
        if "No video formats found" not in str(e) and "no formats" not in str(e).lower():
            raise
        # Fall through — this is likely an image-only pin/post, not a video.

    # Image fallback: some Pinterest pins (and similar) are images, not videos.
    info_opts = _base_ydl_opts()
    info_opts["skip_download"] = True
    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    image_url = info.get("url")
    if not image_url:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            image_url = thumbs[-1].get("url")

    if not image_url:
        return []

    import requests
    resp = requests.get(image_url, timeout=20)
    resp.raise_for_status()
    ext = ".png" if "png" in resp.headers.get("Content-Type", "") else ".jpg"
    out_path = Path(config.DOWNLOAD_DIR) / f"{info.get('id', 'image')}{ext}"
    out_path.write_bytes(resp.content)
    return [str(out_path)]


# --- YouTube: real available qualities --------------------------------

def get_available_qualities(url: str) -> list[tuple[str, str]]:
    """Inspect the actual video and return only the resolutions that
    genuinely exist for it (plus an audio-only option), instead of a
    fixed guess list."""
    import yt_dlp

    opts = _base_ydl_opts()
    opts["skip_download"] = True

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get("formats") or []
    heights = set()
    has_audio = False

    for f in formats:
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        height = f.get("height")

        if vcodec and vcodec != "none" and height:
            heights.add(int(height))
        if acodec and acodec != "none":
            has_audio = True

    qualities: list[tuple[str, str]] = []
    if has_audio:
        qualities.append(("audio", "🎵 Audio (song)"))

    for h in sorted(heights):
        qualities.append((str(h), f"🎬 {h}p"))

    if not qualities:
        # Extremely rare fallback if we couldn't read formats at all.
        qualities = [("best", "🎬 Best available")]

    return qualities


def download_youtube(url: str, quality: str) -> tuple[list[str], str]:
    """Download a YouTube video at the requested quality.
    Returns (paths, media_type) where media_type is 'audio' or 'video'.
    Uses progressive (single-file) formats only, so no ffmpeg merge step
    is required — keeps this reliable on lightweight hosting."""
    import yt_dlp

    opts = _base_ydl_opts()

    if quality == "audio":
        opts["format"] = "bestaudio/best"
        media_type = "audio"
    elif quality == "best":
        opts["format"] = "best"
        media_type = "video"
    else:
        opts["format"] = f"best[height<={quality}][ext=mp4]/best[height<={quality}]/best"
        media_type = "video"

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        p = Path(filename)
        return ([str(p)] if p.exists() else []), media_type
