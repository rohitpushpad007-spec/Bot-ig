import asyncio
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, ReactionTypeEmoji
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
import ig_client
import other_downloads

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IG_URL_RE = re.compile(r"https?://(?:www\.)?instagram\.com/\S+")
OTHER_URL_RE = re.compile(
    r"https?://(?:www\.)?(youtube\.com|youtu\.be|pinterest\.[a-z.]+|pin\.it)/\S+"
)
ANY_URL_RE = re.compile(r"https?://\S+")

# Note: Telegram only allows a fixed set of reaction emojis for bots.
# A robotic-arm-style emoji (like the one you saw) isn't in that set, so we
# use a close, allowed substitute to signal "processing".
