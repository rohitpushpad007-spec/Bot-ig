import os
import re
import logging
import itertools
from pathlib import Path

from instagrapi import Client
from instagrapi.exceptions import LoginRequired

import config

logger = logging.getLogger(__name__)

Path(config.DOWNLOAD_DIR).mkdir(exist_ok=True)

# --- Multi-account pool ----------------------------------------------------
# Each account gets its own Client + its own saved session file, so a
# problem with one account's session doesn't affect the others.

clients: list[Client] = []


def _make_client(sessionid: str, index: int) -> Client:
    c = Client()
    c.delay_range = [1, 3]
    session_file = f"ig_session_{index}.json"

    if os.path.exists(session_file):
        try:
            c.load_settings(session_file)
            c.login_by_sessionid(sessionid)
            c.get_timeline_feed()
            logger.info(f"Account #{index}: logged in using saved session.")
            return c
        except Exception as e:
            logger.warning(f"Account #{index}: saved session failed ({e}), fresh login.")

    c.login_by_sessionid(sessionid)
    c.dump_settings(session_file)
    logger.info(f"Account #{index}: fresh login via sessionid successful.")
    return c


def login():
    """Log in to every configured account."""
    if not config.IG_SESSIONIDS:
        raise RuntimeError("No IG_SESSIONID_1 / IG_SESSIONID_2 ... found in environment.")

    for i, sid in enumerate(config.IG_SESSIONIDS, start=1):
        try:
            clients.append(_make_client(sid, i))
        except Exception as e:
            logger.error(f"Account #{i}: login failed entirely ({e}) — skipping it.")

    if not clients:
        raise RuntimeError("All accounts failed to log in.")

    logger.info(f"{len(clients)} Instagram account(s) ready.")


_round_robin = None


def get_client() -> Client:
    """Return the next client in round-robin order."""
    global _round_robin
    if _round_robin is None:
        _round_robin = itertools.cycle(clients)
    return next(_round_robin)


# --- Link classification -------------------------------------------------

PATTERNS = {
    "post": re.compile(r"instagram\.com/p/([A-Za-z0-9_-]+)"),
    "reel": re.compile(r"instagram\.com/reel[s]?/([A-Za-z0-9_-]+)"),
    "tv": re.compile(r"instagram\.com/tv/([A-Za-z0-9_-]+)"),
    "story": re.compile(r"instagram\.com/stories/([^/]+)/(\d+)"),
    "story_user": re.compile(r"instagram\.com/stories/([^/]+)/?$"),
    "highlight": re.compile(r"instagram\.com/stories/highlights/(\d+)"),
    "share": re.compile(r"instagram\.com/s/([A-Za-z0-9_-]+)"),
}


def classify(url: str) -> str | None:
    for kind, pattern in PATTERNS.items():
        if pattern.search(url):
            return kind
    return None


# --- Downloaders (each picks the next account automatically) ---------------

def download_post_or_reel(url: str) -> list[str]:
    cl = get_client()
    pk = cl.media_pk_from_url(url)
    info = cl.media_info(pk)
    paths = []

    if info.media_type == 1:
        p = cl.photo_download(pk, folder=config.DOWNLOAD_DIR)
        paths.append(str(p))
    elif info.media_type == 2:
        p = cl.video_download(pk, folder=config.DOWNLOAD_DIR)
        paths.append(str(p))
    elif info.media_type == 8:
        ps = cl.album_download(pk, folder=config.DOWNLOAD_DIR)
        paths.extend(str(p) for p in ps)

    return paths


def download_story_by_url(url: str) -> list[str]:
    cl = get_client()
    story_pk = cl.story_pk_from_url(url)
    path = cl.story_download(story_pk, folder=config.DOWNLOAD_DIR)
    return [str(path)]


def download_all_user_stories(username: str) -> list[str]:
    cl = get_client()
    user_id = cl.user_id_from_username(username)
    stories = cl.user_stories(user_id)
    paths = []
    for story in stories:
        p = cl.story_download(story.pk, folder=config.DOWNLOAD_DIR)
        paths.append(str(p))
    return paths


def download_highlight(highlight_pk: str) -> list[str]:
    cl = get_client()
    info = cl.highlight_info(f"highlight:{highlight_pk}")
    paths = []
    for item in info.items:
        p = cl.story_download(item.pk, folder=config.DOWNLOAD_DIR)
        paths.append(str(p))
    return paths


def resolve_share_link(url: str) -> str:
    import requests
    resp = requests.head(url, allow_redirects=True, timeout=15)
    return resp.url
