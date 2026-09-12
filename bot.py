import asyncio
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
import ig_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://(?:www\.)?instagram\.com/\S+")


class _PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - bot is running")

    def log_message(self, *args):
        pass  # silence default request logging


def start_ping_server():
    """Tiny HTTP server so Render treats this as a Web Service and
    UptimeRobot has something to ping to keep it awake."""
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _PingHandler)
    logger.info(f"Ping server listening on port {port}")
    server.serve_forever()


def allowed(update: Update) -> bool:
    if config.ALLOWED_USER_ID is None:
        return True
    return update.effective_user and update.effective_user.id == config.ALLOWED_USER_ID


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Instagram link bhejo (post / reel / story / highlight), "
        "main download karke bhej dunga."
    )


async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text("⛔ Ye bot sirf owner ke liye hai.")
        return

    url_match = URL_RE.search(update.message.text)
    url = url_match.group(0)
    kind = ig_client.classify(url)

    if kind is None:
        await update.message.reply_text("❌ Ye Instagram link samajh nahi aaya.")
        return

    status = await update.message.reply_text("⏳ Download ho raha hai...")

    try:
        paths: list[str] = []

        if kind == "share":
            url = await asyncio.to_thread(ig_client.resolve_share_link, url)
            kind = ig_client.classify(url)

        if kind in ("post", "reel", "tv"):
            paths = await asyncio.to_thread(ig_client.download_post_or_reel, url)
        elif kind == "story":
            paths = await asyncio.to_thread(ig_client.download_story_by_url, url)
        elif kind == "story_user":
            m = ig_client.PATTERNS["story_user"].search(url)
            username = m.group(1)
            paths = await asyncio.to_thread(ig_client.download_all_user_stories, username)
        elif kind == "highlight":
            m = ig_client.PATTERNS["highlight"].search(url)
            highlight_pk = m.group(1)
            paths = await asyncio.to_thread(ig_client.download_highlight, highlight_pk)
        else:
            await status.edit_text("❌ Ye link type abhi supported nahi hai.")
            return

        if not paths:
            await status.edit_text("❌ Kuch download nahi hua — link private ho sakta hai ya format badal gaya hai.")
            return

        await status.edit_text(f"📤 Bhej raha hoon ({len(paths)} file)...")

        for p in paths:
            with open(p, "rb") as f:
                if p.lower().endswith((".mp4", ".mov")):
                    await update.message.reply_video(f)
                else:
                    await update.message.reply_photo(f)
            os.remove(p)  # cleanup after sending

        await status.delete()

    except Exception as e:
        logger.exception("Download failed")
        await status.edit_text(f"❌ Error: {e}")


async def fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Instagram link bhejo (post/reel/story/highlight link).")


async def post_init(app):
    logger.info("Logging in to Instagram...")
    await asyncio.to_thread(ig_client.login)
    logger.info("Instagram login done.")


def main():
    # Python 3.14 removed the implicit event-loop auto-creation that
    # python-telegram-bot's run_polling() still relies on internally.
    # Create and set one explicitly before starting the bot.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Start the tiny HTTP ping server in the background (needed on Render).
    threading.Thread(target=start_ping_server, daemon=True).start()

    app = ApplicationBuilder().token(config.BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.Regex(URL_RE), link_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_handler))

    logger.info("Starting bot polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
