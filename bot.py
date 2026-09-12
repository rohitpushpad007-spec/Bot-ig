import asyncio
import logging
import os
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    ReactionTypeEmoji,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import config
import ig_client
import other_downloads

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IG_URL_RE = re.compile(r"https?://(?:www\.)?instagram\.com/\S+")
ANY_URL_RE = re.compile(r"https?://\S+")

# Telegram bots can only use a fixed set of reaction emojis — a custom
# robotic-arm style reaction isn't in that set, so we use close allowed ones.
REACTION_PROCESSING = "⚡"
REACTION_SUCCESS = "🎉"

CAPTION_LIMIT = 1024  # Telegram's max caption length for photo/video

# Maps a short id -> YouTube URL, so callback buttons don't need to carry
# the full URL (Telegram limits callback_data to 64 bytes).
pending_youtube: dict[str, str] = {}


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


def friendly_error(e: Exception) -> str:
    msg = str(e)
    if "Sign in to confirm" in msg or "not a bot" in msg.lower():
        return "YouTube is blocking downloads from this server right now. Try again later, or send a different link."
    if "No video formats found" in msg or "no formats" in msg.lower():
        return "Couldn't find a downloadable file for this link."
    if "private" in msg.lower():
        return "This looks like private content — I can't access it."
    if "loginrequired" in msg.lower():
        return "The account session expired — this needs to be refreshed."
    return "Something went wrong while downloading this link."


def trim_caption(caption: str | None) -> str | None:
    if not caption:
        return None
    if len(caption) <= CAPTION_LIMIT:
        return caption
    return caption[: CAPTION_LIMIT - 1].rstrip() + "…"


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Welcome!*\n\n"
        "Send me a link and I'll fetch it for you. Here's everything I can do:\n\n"
        "📸 *Instagram*\n"
        "• Posts, Reels & IGTV\n"
        "• Stories & Highlights\n"
        "• Full captions included with every download\n\n"
        "▶️ *YouTube*\n"
        "• Choose 🎵 audio-only (song) or 🎬 video\n"
        "• Shows the *real* qualities available for that exact video (144p up to whatever it has — 1080p, 4K, etc.)\n\n"
        "📌 *Pinterest*\n"
        "• Videos and images — auto-detected\n\n"
        "🔗 Just paste a link below to get started. ✨",
        parse_mode="Markdown",
    )


async def send_and_cleanup(update: Update, paths: list[str], caption: str | None = None):
    caption = trim_caption(caption)
    for i, p in enumerate(paths):
        cap = caption if i == 0 else None
        with open(p, "rb") as f:
            if p.lower().endswith((".mp4", ".mov", ".webm")):
                await update.message.reply_video(f, caption=cap)
            elif p.lower().endswith((".mp3", ".m4a", ".opus", ".aac", ".ogg")):
                await update.message.reply_audio(f, caption=cap)
            else:
                await update.message.reply_photo(f, caption=cap)
        os.remove(p)


async def react(context, chat_id, message_id, emoji):
    try:
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception:
        pass  # reactions are a nice-to-have, never block on this


async def link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text("⛔ *This bot is private.*", parse_mode="Markdown")
        return

    url_match = ANY_URL_RE.search(update.message.text)
    url = url_match.group(0)

    is_ig = bool(IG_URL_RE.search(url))
    other_kind = other_downloads.classify_other(url)

    if not is_ig and not other_kind:
        await update.message.reply_text(
            "❌ *Unrecognized link*\n\nI only support Instagram, YouTube, and Pinterest links.",
            parse_mode="Markdown",
        )
        return

    await react(context, update.effective_chat.id, update.message.message_id, REACTION_PROCESSING)

    # YouTube gets a quality picker based on what's actually available.
    if other_kind == "youtube":
        checking = await update.message.reply_text("🔎 *Checking available qualities...*", parse_mode="Markdown")

        try:
            qualities = await asyncio.to_thread(other_downloads.get_available_qualities, url)
        except Exception as e:
            logger.exception("Could not fetch qualities")
            await checking.edit_text(
                f"❌ *Couldn't read this video*\n\n_{friendly_error(e)}_", parse_mode="Markdown"
            )
            return

        short_id = uuid.uuid4().hex[:8]
        pending_youtube[short_id] = url

        rows = []
        row = []
        for quality_id, label in qualities:
            if quality_id == "audio":
                if row:
                    rows.append(row)
                    row = []
                rows.append([InlineKeyboardButton(label, callback_data=f"yt|{short_id}|{quality_id}")])
                continue
            row.append(InlineKeyboardButton(label, callback_data=f"yt|{short_id}|{quality_id}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

        await checking.edit_text(
            "🎬 *Pick a quality:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    status = await update.message.reply_text("⚡ *Fetching your content...*", parse_mode="Markdown")

    try:
        paths: list[str] = []
        caption: str | None = None

        if is_ig:
            kind = ig_client.classify(url)

            if kind == "share":
                url = await asyncio.to_thread(ig_client.resolve_share_link, url)
                kind = ig_client.classify(url)

            if kind in ("post", "reel", "tv"):
                paths, caption = await asyncio.to_thread(ig_client.download_post_or_reel, url)
            elif kind == "story":
                paths, caption = await asyncio.to_thread(ig_client.download_story_by_url, url)
            elif kind == "story_user":
                m = ig_client.PATTERNS["story_user"].search(url)
                username = m.group(1)
                paths, caption = await asyncio.to_thread(ig_client.download_all_user_stories, username)
            elif kind == "highlight":
                m = ig_client.PATTERNS["highlight"].search(url)
                highlight_pk = m.group(1)
                paths, caption = await asyncio.to_thread(ig_client.download_highlight, highlight_pk)
            else:
                await status.edit_text(
                    "❌ *Not supported yet*\n\nThis Instagram link type isn't handled yet.",
                    parse_mode="Markdown",
                )
                return
        else:
            # Pinterest via yt-dlp (with image fallback)
            paths = await asyncio.to_thread(other_downloads.download_with_ytdlp, url)

        if not paths:
            await status.edit_text(
                "❌ *Couldn't download this*\n\n_The link might be private, deleted, or unsupported._",
                parse_mode="Markdown",
            )
            return

        await status.edit_text("📤 *Sending your file...*", parse_mode="Markdown")
        await send_and_cleanup(update, paths, caption)
        await status.edit_text("✅ *Done!*", parse_mode="Markdown")
        await react(context, update.effective_chat.id, update.message.message_id, REACTION_SUCCESS)

    except Exception as e:
        logger.exception("Download failed")
        await status.edit_text(f"❌ *Couldn't complete this download*\n\n_{friendly_error(e)}_", parse_mode="Markdown")


async def youtube_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _, short_id, quality = query.data.split("|", 2)
    except ValueError:
        await query.edit_message_text("❌ *Something went wrong — please resend the link.*", parse_mode="Markdown")
        return

    url = pending_youtube.pop(short_id, None)
    if not url:
        await query.edit_message_text(
            "❌ *This button expired — please resend the link.*", parse_mode="Markdown"
        )
        return

    label = "🎵 Audio" if quality == "audio" else ("🎬 Best available" if quality == "best" else f"🎬 {quality}p")
    await query.edit_message_text(f"⚡ *Downloading {label}...*", parse_mode="Markdown")

    try:
        paths, media_type = await asyncio.to_thread(other_downloads.download_youtube, url, quality)

        if not paths:
            await query.edit_message_text(
                "❌ *Couldn't download this*\n\n_This quality might not be available for this video._",
                parse_mode="Markdown",
            )
            return

        await query.edit_message_text("📤 *Sending your file...*", parse_mode="Markdown")

        for p in paths:
            with open(p, "rb") as f:
                if media_type == "audio":
                    await context.bot.send_audio(chat_id=query.message.chat_id, audio=f)
                else:
                    await context.bot.send_video(chat_id=query.message.chat_id, video=f)
            os.remove(p)

        await query.edit_message_text("✅ *Done!*", parse_mode="Markdown")

    except Exception as e:
        logger.exception("YouTube download failed")
        await query.edit_message_text(
            f"❌ *Couldn't complete this download*\n\n_{friendly_error(e)}_", parse_mode="Markdown"
        )


async def fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send me an Instagram, YouTube, or Pinterest link. 🔗")


async def post_init(app):
    logger.info("Logging in to Instagram...")
    await asyncio.to_thread(ig_client.login)
    logger.info("Instagram login done.")


def main():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    threading.Thread(target=start_ping_server, daemon=True).start()

    app = ApplicationBuilder().token(config.BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(youtube_quality_callback, pattern=r"^yt\|"))
    app.add_handler(MessageHandler(filters.Regex(ANY_URL_RE), link_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_handler))

    logger.info("Starting bot polling...")
    app.run_polling()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        import traceback
        print("FATAL ERROR CAUGHT:", flush=True)
        traceback.print_exc()
        raise
