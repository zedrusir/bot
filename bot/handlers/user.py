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
    MSG_DONE,
    MSG_DOWNLOADING,
    MSG_ERROR_FETCH,
    MSG_ERROR_GENERAL,
    MSG_FETCHING_INFO,
    MSG_INVALID_URL,
    MSG_QUALITY_SELECTION,
    MSG_RATE_LIMITED,
    MSG_SESSION_EXPIRED,
    MSG_START,
    MSG_UPLOADING,
)
from bot.utils.progress import ProgressUpdater, make_progress_bar
from bot.utils.rate_limiter import rate_limiter

# Matches youtube.com and youtu.be URLs (with or without https/www)
_YT_RE = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?.*v=|shorts/|embed/)|youtu\.be/)[\w\-]+"
)


# ─── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await db.add_or_update_user(
        user.id, user.username or "", user.first_name or ""
    )
    await update.message.reply_text(
        MSG_START.format(name=_escape_md(user.first_name or "کاربر")),
        parse_mode="MarkdownV2",
    )


# ─── Incoming URL message ─────────────────────────────────────────────────────

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (update.message.text or "").strip()

    await db.add_or_update_user(
        user.id, user.username or "", user.first_name or ""
    )

    if await db.is_banned(user.id):
        await update.message.reply_text(MSG_BANNED, parse_mode="MarkdownV2")
        return

    if not _YT_RE.search(text):
        await update.message.reply_text(MSG_INVALID_URL, parse_mode="MarkdownV2")
        return

    if rate_limiter.is_at_limit(user.id):
        await update.message.reply_text(
            MSG_RATE_LIMITED.format(
                count=rate_limiter.get_active_count(user.id),
                max=config.MAX_CONCURRENT_PER_USER,
            ),
            parse_mode="MarkdownV2",
        )
        return

    status_msg = await update.message.reply_text(
        MSG_FETCHING_INFO, parse_mode="MarkdownV2"
    )

    try:
        info = await get_video_info(text)
    except Exception as exc:
        logger.warning("get_video_info failed for {}: {}", text, exc)
        await status_msg.edit_text(MSG_ERROR_FETCH, parse_mode="MarkdownV2")
        return

    title = (info.get("title") or "ویدیو")[:60]
    duration = format_duration(info.get("duration"))
    views = format_views(info.get("view_count"))

    # Persist URL for the callback
    context.user_data["pending_url"] = text
    context.user_data["pending_title"] = title

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📺 360p", callback_data="q_360p"),
                InlineKeyboardButton("🎬 720p HD", callback_data="q_720p"),
            ],
            [
                InlineKeyboardButton("🎥 1080p Full HD", callback_data="q_1080p"),
                InlineKeyboardButton("⭐ بهترین کیفیت", callback_data="q_best"),
            ],
            [InlineKeyboardButton("❌ لغو", callback_data="q_cancel")],
        ]
    )

    await status_msg.edit_text(
        MSG_QUALITY_SELECTION.format(
            title=_escape_md(title),
            duration=duration,
            views=views,
        ),
        reply_markup=keyboard,
        parse_mode="MarkdownV2",
    )


# ─── Quality selection callback ───────────────────────────────────────────────

async def handle_quality_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    data: str = query.data  # type: ignore[assignment]

    if data == "q_cancel":
        context.user_data.pop("pending_url", None)
        context.user_data.pop("pending_title", None)
        await query.edit_message_text(MSG_CANCELLED, parse_mode="MarkdownV2")
        return

    quality = data.removeprefix("q_")  # "360p" | "720p" | "1080p" | "best"
    url: str | None = context.user_data.pop("pending_url", None)
    title: str = context.user_data.pop("pending_title", "ویدیو")

    if not url:
        await query.edit_message_text(
            MSG_SESSION_EXPIRED, parse_mode="MarkdownV2"
        )
        return

    if rate_limiter.is_at_limit(user.id):
        await query.edit_message_text(
            MSG_RATE_LIMITED.format(
                count=rate_limiter.get_active_count(user.id),
                max=config.MAX_CONCURRENT_PER_USER,
            ),
            parse_mode="MarkdownV2",
        )
        return

    # Fire-and-forget pipeline (doesn't block the handler)
    asyncio.create_task(
        _pipeline(
            user_id=user.id,
            url=url,
            quality=quality,
            title=title,
            message=query.message,
        )
    )


# ─── Download / upload pipeline ───────────────────────────────────────────────

async def _pipeline(
    user_id: int,
    url: str,
    quality: str,
    title: str,
    message,
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
                )
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
            )
        )

        dl = await download_video(url, quality, _on_dl)
        download_dir = dl["download_dir"]
        file_path: str = dl["file_path"]
        size_mb: float = dl["size_mb"]
        final_title: str = dl["title"]

        # ── Phase 2: Upload ─────────────────────────────────────────────────
        async def _on_up(*, percent, **_):
            await progress.update(
                MSG_UPLOADING.format(
                    title=_escape_md(final_title[:45]),
                    size=_fmt_size(size_mb),
                    progress_bar=make_progress_bar(percent),
                    percent=percent,
                )
            )

        await progress.force_update(
            MSG_UPLOADING.format(
                title=_escape_md(final_title[:45]),
                size=_fmt_size(size_mb),
                progress_bar=make_progress_bar(0),
                percent=0,
            )
        )

        drive = await upload_to_drive(file_path, user_id, _on_up)

        # ── Persist & notify ────────────────────────────────────────────────
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
            )
        )
        logger.info(
            "Pipeline done for user {} — '{}' ({:.1f} MB)",
            user_id, final_title, size_mb,
        )

    except Exception as exc:
        logger.error("Pipeline failed for user {}: {}", user_id, exc, exc_info=True)
        if download_id:
            await db.update_download(download_id, status="failed")
        try:
            await message.edit_text(MSG_ERROR_GENERAL, parse_mode="MarkdownV2")
        except Exception:
            pass

    finally:
        rate_limiter.release(user_id)
        if download_dir:
            shutil.rmtree(download_dir, ignore_errors=True)
            logger.debug("Cleaned up {}", download_dir)


# ─── Tiny helpers ─────────────────────────────────────────────────────────────

def _fmt_size(mb: float) -> str:
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


# Characters that must be escaped in MarkdownV2 plain text (not inside `…`)
_MD_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _escape_md(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


# ─── Handler list (imported by main.py) ───────────────────────────────────────

user_handlers = [
    CommandHandler("start", cmd_start),
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url),
    CallbackQueryHandler(handle_quality_callback, pattern=r"^q_"),
]
