print("STEP 1: python started", flush=True)

import asyncio
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

print("STEP 2: stdlib imports done", flush=True)

from telegram import Update, ReactionTypeEmoji
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

print("STEP 3: python-telegram-bot imported", flush=True)

import config

print("STEP 4: config imported", flush=True)

import ig_client

print("STEP 5: ig_client (instagrapi) imported", flush=True)

import other_downloads

print("STEP 6: other_downloads (yt-dlp) imported", flush=True)

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
REACTION_PROCESSING = "⚡"
REACTION_SUCCESS = "🎉"
REACTION_FAILED = "😢"


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
        "👋 Hello!\n\n"
        "📥 I can help you download videos and images from:\n\n"
        "📸 Instagram (post / reel / story / highlight)\n"
        "▶️ YouTube\n"
        "📌 Pinterest\n\n"
        "• Just send me a link to download."
    )


async def send_and_cleanup(update: Update, paths: list[str]):
    for p in paths:
        with open(p, "rb") as f:
            if p.lower().endswith((".mp4", ".mov", ".webm")):
                await update.message.reply_video(f)
            else:
                await update.message.reply_photo(f)
        os.remove(p)


async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text("⛔ This bot is private.")
        return

    url_match = ANY_URL_RE.search(update.message.text)
    url = url_match.group(0)

    is_ig = bool(IG_URL_RE.search(url))
    other_kind = other_downloads.classify_other(url)

    if not is_ig and not other_kind:
        await update.message.reply_text("❌ Sorry, I don't recognize this link.")
        return

    # React on the user's message to show we've started processing.
    try:
        await context.bot.set_message_reaction(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            reaction=[ReactionTypeEmoji(emoji=REACTION_PROCESSING)],
        )
    except Exception:
        pass  # reactions are a nice-to-have, never block on this

    status = await update.message.reply_text("⏳ Downloading...")

    try:
        paths: list[str] = []

        if is_ig:
            kind = ig_client.classify(url)

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
                await status.edit_text("❌ This Instagram link type isn't supported yet.")
                return
        else:
            # YouTube / Pinterest via yt-dlp
            paths = await asyncio.to_thread(other_downloads.download_with_ytdlp, url)

        if not paths:
            await status.edit_text("❌ Couldn't download anything — the link might be private or unsupported.")
            return

        await status.edit_text(f"📤 Sending ({len(paths)} file{'s' if len(paths) > 1 else ''})...")
        await send_and_cleanup(update, paths)
        await status.delete()

        try:
            await context.bot.set_message_reaction(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                reaction=[ReactionTypeEmoji(emoji=REACTION_SUCCESS)],
            )
        except Exception:
            pass

    except Exception as e:
        logger.exception("Download failed")
        await status.edit_text(f"❌ Error: {e}")


async def fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send me an Instagram, YouTube, or Pinterest link.")


async def post_init(app):
    print("STEP 8: post_init starting Instagram login...", flush=True)
    await asyncio.to_thread(ig_client.login)
    print("STEP 9: Instagram login done", flush=True)


def main():
    print("STEP 7: main() starting", flush=True)

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    threading.Thread(target=start_ping_server, daemon=True).start()
    print("STEP 7b: ping server thread started", flush=True)

    app = ApplicationBuilder().token(config.BOT_TOKEN).post_init(post_init).build()
    print("STEP 7c: application built", flush=True)

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.Regex(ANY_URL_RE), link_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_handler))

    print("STEP 10: calling run_polling()", flush=True)
    app.run_polling()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        import traceback
        print("FATAL ERROR CAUGHT:", flush=True)
        traceback.print_exc()
        raise
