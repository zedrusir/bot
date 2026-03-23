import asyncio
import re
import shutil

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.config import config
from bot.services.database import db
from bot.services.downloader import (
    QUALITY_LABELS,
    download_video,
    format_duration,
    format_views,
    get_video_info,
)
from bot.services.uploader import upload_to_drive
from bot.utils.messages import (
    MSG_BANNED,
    MSG_CANCELLED,
    MSG_DESTINATION_SELECTION,
    MSG_DONE,
    MSG_DONE_TELEGRAM,
    MSG_DOWNLOADING,
    MSG_ERROR_FETCH,
    MSG_ERROR_GENERAL,
    MSG_FETCHING_INFO,
    MSG_HELP,
    MSG_HISTORY_EMPTY,
    MSG_HISTORY_HEADER,
    MSG_INVALID_URL,
    MSG_QUALITY_SELECTION,
    MSG_RATE_LIMITED,
    MSG_SENDING_TELEGRAM,
    MSG_SESSION_EXPIRED,
    MSG_START,
    MSG_TOO_LARGE_TELEGRAM,
    MSG_UPLOADING,
    MSG_VERSION_FOOTER,
)
from bot.config import VERSION
from bot.utils.progress import ProgressUpdater, make_progress_bar
from bot.utils.rate_limiter import rate_limiter

# Matches youtube.com and youtu.be URLs (with or without https/www)
_YT_RE = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?.*v=|shorts/|embed/)|youtu\.be/)[\w\-]+"
)

_QUALITY_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("📺 360p", callback_data="q_360p"),
            InlineKeyboardButton("🎬 720p HD", callback_data="q_720p"),
        ],
        [
            InlineKeyboardButton("🎥 1080p Full HD", callback_data="q_1080p"),
            InlineKeyboardButton("⭐ بهترین کیفیت", callback_data="q_best"),
        ],
        [InlineKeyboardButton("🎵 فقط صدا (MP3)", callback_data="q_audio")],
        [InlineKeyboardButton("❌ لغو", callback_data="q_cancel")],
    ]
)

_DESTINATION_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("☁️ Google Drive", callback_data="dest_drive"),
            InlineKeyboardButton("📱 تلگرام", callback_data="dest_telegram"),
        ],
        [InlineKeyboardButton("❌ لغو", callback_data="dest_cancel")],
    ]
)

_RETRY_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🔄 ارسال مجدد لینک", callback_data="retry_hint")]]
)

_TELEGRAM_MAX_MB = 50.0


# ─── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await db.add_or_update_user(
        user.id, user.username or "", user.first_name or ""
    )
    await update.message.reply_text(
        MSG_START.format(name=_escape_md(user.first_name or "کاربر")) + _footer(),
        parse_mode="MarkdownV2",
    )


# ─── /help ────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        MSG_HELP + _footer(),
        parse_mode="MarkdownV2",
    )


# ─── /history ─────────────────────────────────────────────────────────────────

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    history = await db.get_user_history(user.id, limit=5)

    if not history:
        await update.message.reply_text(
            MSG_HISTORY_EMPTY + _footer(), parse_mode="MarkdownV2"
        )
        return

    lines = [MSG_HISTORY_HEADER]
    for i, item in enumerate(history, 1):
        title = _escape_md(item["title"][:45])
        quality = _escape_md(QUALITY_LABELS.get(item["quality"], item["quality"]))
        size = _escape_md(_fmt_size(item["size"]))
        link = item["link"]
        lines.append(f"{i}\\. [{title}]({link})\n   {quality} \\| `{size}`\n\n")

    await update.message.reply_text(
        "".join(lines) + _footer(),
        parse_mode="MarkdownV2",
        disable_web_page_preview=True,
    )


# ─── Incoming URL message ─────────────────────────────────────────────────────

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (update.message.text or "").strip()

    await db.add_or_update_user(
        user.id, user.username or "", user.first_name or ""
    )

    if await db.is_banned(user.id):
        await update.message.reply_text(MSG_BANNED + _footer(), parse_mode="MarkdownV2")
        return

    if not _YT_RE.search(text):
        await update.message.reply_text(MSG_INVALID_URL + _footer(), parse_mode="MarkdownV2")
        return

    if rate_limiter.is_at_limit(user.id):
        await update.message.reply_text(
            MSG_RATE_LIMITED.format(
                count=rate_limiter.get_active_count(user.id),
                max=config.MAX_CONCURRENT_PER_USER,
            ) + _footer(),
            parse_mode="MarkdownV2",
        )
        return

    status_msg = await update.message.reply_text(
        MSG_FETCHING_INFO + _footer(), parse_mode="MarkdownV2"
    )

    try:
        info = await get_video_info(text)
    except Exception as exc:
        logger.warning("get_video_info failed for {}: {}", text, exc)
        await status_msg.edit_text(MSG_ERROR_FETCH + _footer(), parse_mode="MarkdownV2")
        return

    title = (info.get("title") or "ویدیو")[:60]
    duration = format_duration(info.get("duration"))
    views = format_views(info.get("view_count"))
    thumbnail = info.get("thumbnail")

    context.user_data["pending_url"] = text
    context.user_data["pending_title"] = title

    caption = (
        MSG_QUALITY_SELECTION.format(
            title=_escape_md(title),
            duration=_escape_md(duration),
            views=_escape_md(views),
        )
        + _footer()
    )

    # Send photo with caption + keyboard as a single message
    if thumbnail:
        try:
            await status_msg.delete()
            await update.message.reply_photo(
                photo=thumbnail,
                caption=caption,
                parse_mode="MarkdownV2",
                reply_markup=_QUALITY_KEYBOARD,
            )
            return
        except Exception:
            pass  # fall through to text message

    # Fallback: text message with keyboard
    await status_msg.edit_text(
        caption,
        reply_markup=_QUALITY_KEYBOARD,
        parse_mode="MarkdownV2",
    )


# ─── Quality selection callback ───────────────────────────────────────────────

async def handle_quality_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()

    data: str = query.data  # type: ignore[assignment]

    if data == "q_cancel":
        context.user_data.pop("pending_url", None)
        context.user_data.pop("pending_title", None)
        await _edit_message(query.message, MSG_CANCELLED + _footer())
        return

    quality = data.removeprefix("q_")
    url: str | None = context.user_data.get("pending_url")
    title: str = context.user_data.get("pending_title", "ویدیو")

    if not url:
        await _edit_message(query.message, MSG_SESSION_EXPIRED + _footer())
        return

    context.user_data["pending_quality"] = quality

    qlabel = QUALITY_LABELS.get(quality, quality)
    dest_caption = (
        MSG_DESTINATION_SELECTION.format(
            title=_escape_md(title),
            quality=_escape_md(qlabel),
        )
        + _footer()
    )

    await _edit_message(query.message, dest_caption, reply_markup=_DESTINATION_KEYBOARD)


# ─── Destination selection callback ──────────────────────────────────────────

async def handle_destination_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    data: str = query.data  # type: ignore[assignment]

    if data == "dest_cancel":
        context.user_data.pop("pending_url", None)
        context.user_data.pop("pending_title", None)
        context.user_data.pop("pending_quality", None)
        await _edit_message(query.message, MSG_CANCELLED + _footer())
        return

    destination = data.removeprefix("dest_")  # "drive" | "telegram"
    url: str | None = context.user_data.pop("pending_url", None)
    title: str = context.user_data.pop("pending_title", "ویدیو")
    quality: str = context.user_data.pop("pending_quality", "best")

    if not url:
        await _edit_message(query.message, MSG_SESSION_EXPIRED + _footer())
        return

    if rate_limiter.is_at_limit(user.id):
        await _edit_message(
            query.message,
            MSG_RATE_LIMITED.format(
                count=rate_limiter.get_active_count(user.id),
                max=config.MAX_CONCURRENT_PER_USER,
            ) + _footer(),
        )
        return

    # Remove keyboard, send a fresh text message for progress tracking
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    status_msg = await query.message.reply_text(
        "⏳ در حال شروع\\.\\.\\." + _footer(), parse_mode="MarkdownV2"
    )

    asyncio.create_task(
        _pipeline(
            user_id=user.id,
            url=url,
            quality=quality,
            title=title,
            message=status_msg,
            destination=destination,
            chat_id=update.effective_chat.id,
            context=context,
        )
    )


# ─── Retry hint callback ──────────────────────────────────────────────────────

async def handle_retry_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔄 لینک یوتیوب را دوباره ارسال کنید\\." + _footer(),
        parse_mode="MarkdownV2",
    )


# ─── Download / upload pipeline ───────────────────────────────────────────────

async def _pipeline(
    user_id: int,
    url: str,
    quality: str,
    title: str,
    message,
    destination: str,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await rate_limiter.acquire(user_id)
    download_dir: str | None = None
    download_id: int | None = None

    try:
        download_id = await db.add_download(user_id, url, title, quality)
        progress = ProgressUpdater(message, min_interval=4.0)
        qlabel = QUALITY_LABELS.get(quality, quality)

        # ── Phase 1: Download ───────────────────────────────────────────────
        async def _on_dl(*, percent, downloaded, total, speed, eta, **_):
            speed_s = f"{speed / 1_048_576:.1f} MB/s" if speed else "..."
            dl_s = f"{downloaded / 1_048_576:.1f} MB"
            total_s = f"{total / 1_048_576:.1f} MB" if total else "؟"
            eta_s = f"{eta}s" if eta else "..."
            await progress.update(
                MSG_DOWNLOADING.format(
                    title=_escape_md(title[:45]),
                    quality=qlabel,
                    progress_bar=make_progress_bar(percent),
                    speed=speed_s,
                    downloaded=dl_s,
                    total=total_s,
                    eta=eta_s,
                ) + _footer()
            )

        await progress.force_update(
            MSG_DOWNLOADING.format(
                title=_escape_md(title[:45]),
                quality=qlabel,
                progress_bar=make_progress_bar(0),
                speed="...",
                downloaded="0 MB",
                total="؟",
                eta="...",
            ) + _footer()
        )

        dl = await download_video(url, quality, _on_dl)
        download_dir = dl["download_dir"]
        file_path: str = dl["file_path"]
        size_mb: float = dl["size_mb"]
        final_title: str = dl["title"]

        # ── Phase 2: Send to destination ────────────────────────────────────
        if destination == "telegram" and size_mb <= _TELEGRAM_MAX_MB:
            await progress.force_update(
                MSG_SENDING_TELEGRAM.format(
                    title=_escape_md(final_title[:45]),
                    size=_escape_md(_fmt_size(size_mb)),
                ) + _footer()
            )

            with open(file_path, "rb") as f:
                if quality == "audio":
                    await context.bot.send_audio(
                        chat_id=chat_id,
                        audio=f,
                        title=final_title[:64],
                    )
                else:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        caption=f"🎬 {final_title[:200]}",
                        supports_streaming=True,
                    )

            await db.update_download(
                download_id, title=final_title, file_size_mb=size_mb, status="done"
            )
            await progress.force_update(
                MSG_DONE_TELEGRAM.format(
                    title=_escape_md(final_title[:60]),
                    quality=qlabel,
                    size=_escape_md(_fmt_size(size_mb)),
                ) + _footer()
            )

        else:
            # Drive upload (default, or fallback when file > 50 MB for Telegram)
            if destination == "telegram" and size_mb > _TELEGRAM_MAX_MB:
                await progress.force_update(
                    MSG_TOO_LARGE_TELEGRAM.format(
                        size=_escape_md(_fmt_size(size_mb))
                    ) + _footer()
                )
                await asyncio.sleep(3)

            async def _on_up(*, percent, **_):
                await progress.update(
                    MSG_UPLOADING.format(
                        title=_escape_md(final_title[:45]),
                        size=_fmt_size(size_mb),
                        progress_bar=make_progress_bar(percent),
                        percent=percent,
                    ) + _footer()
                )

            await progress.force_update(
                MSG_UPLOADING.format(
                    title=_escape_md(final_title[:45]),
                    size=_fmt_size(size_mb),
                    progress_bar=make_progress_bar(0),
                    percent=0,
                ) + _footer()
            )

            drive = await upload_to_drive(file_path, user_id, _on_up)

            await db.update_download(
                download_id,
                title=final_title,
                file_size_mb=size_mb,
                drive_id=drive["file_id"],
                drive_link=drive["link"],
                status="done",
            )

            await progress.force_update(
                MSG_DONE.format(
                    title=_escape_md(final_title[:60]),
                    quality=qlabel,
                    size=_fmt_size(size_mb),
                    link=drive["link"],
                ) + _footer()
            )

        logger.info(
            "Pipeline done for user {} — '{}' ({:.1f} MB) → {}",
            user_id, final_title, size_mb, destination,
        )

    except Exception as exc:
        logger.error("Pipeline failed for user {}: {}", user_id, exc, exc_info=True)
        if download_id:
            await db.update_download(download_id, status="failed")
        try:
            await message.edit_text(
                MSG_ERROR_GENERAL + _footer(),
                parse_mode="MarkdownV2",
                reply_markup=_RETRY_KEYBOARD,
            )
        except Exception:
            pass

    finally:
        rate_limiter.release(user_id)
        if download_dir:
            shutil.rmtree(download_dir, ignore_errors=True)
            logger.debug("Cleaned up {}", download_dir)


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _edit_message(message, text: str, reply_markup=None) -> None:
    """Edit a message whether it's a photo (caption) or text."""
    try:
        if message.photo:
            await message.edit_caption(
                caption=text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        else:
            await message.edit_text(
                text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
    except Exception:
        pass


def _fmt_size(mb: float) -> str:
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


def _footer() -> str:
    return MSG_VERSION_FOOTER.format(version=_escape_md(VERSION))


_MD_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _escape_md(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


# ─── Handler list (imported by main.py) ───────────────────────────────────────

user_handlers = [
    CommandHandler("start", cmd_start),
    CommandHandler("help", cmd_help),
    CommandHandler("history", cmd_history),
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url),
    CallbackQueryHandler(handle_quality_callback, pattern=r"^q_"),
    CallbackQueryHandler(handle_destination_callback, pattern=r"^dest_"),
    CallbackQueryHandler(handle_retry_callback, pattern=r"^retry_"),
]
