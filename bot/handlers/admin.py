import asyncio
from functools import wraps

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from bot.config import config
from bot.services.database import db
from bot.utils.messages import (
    MSG_BROADCAST_SENT,
    MSG_NOT_ADMIN,
    MSG_STATS,
    MSG_USER_INFO,
    escape_md,
)


# ─── Admin guard decorator ────────────────────────────────────────────────────

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in config.ADMIN_IDS:
            await update.message.reply_text(
                MSG_NOT_ADMIN, parse_mode="MarkdownV2"
            )
            return
        return await func(update, context)
    return wrapper


# ─── /stats ───────────────────────────────────────────────────────────────────

@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stats = await db.get_stats()
    await update.message.reply_text(
        MSG_STATS.format(
            total_users=stats["total_users"],
            total_downloads=stats["total_downloads"],
            total_gb=stats["total_gb"],
            today_downloads=stats["today_downloads"],
        ),
        parse_mode="MarkdownV2",
    )


# ─── /broadcast ───────────────────────────────────────────────────────────────

@admin_only
async def cmd_broadcast(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not context.args:
        await update.message.reply_text(
            "⚠️ استفاده: `/broadcast <پیام>`", parse_mode="MarkdownV2"
        )
        return

    text = "📢 *پیام مدیر:*\n\n" + " ".join(context.args)
    user_ids = await db.get_all_user_ids()

    sent = failed = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(uid, text, parse_mode="Markdown")
            sent += 1
            # Throttle to ~30 messages/second (Telegram bulk limit)
            await asyncio.sleep(0.035)
        except Exception as exc:
            logger.warning("Broadcast failed for {}: {}", uid, exc)
            failed += 1

    await update.message.reply_text(
        MSG_BROADCAST_SENT.format(sent=sent, failed=failed),
        parse_mode="MarkdownV2",
    )


# ─── /ban / /unban ────────────────────────────────────────────────────────────

@admin_only
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "⚠️ استفاده: `/ban <user_id>`", parse_mode="MarkdownV2"
        )
        return
    try:
        target = int(context.args[0])
        await db.ban_user(target)
        await update.message.reply_text(f"✅ کاربر `{target}` مسدود شد\\.", parse_mode="MarkdownV2")
        logger.info("Admin {} banned user {}", update.effective_user.id, target)
    except ValueError:
        await update.message.reply_text("❌ شناسه کاربر باید عدد باشد\\.", parse_mode="MarkdownV2")


@admin_only
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "⚠️ استفاده: `/unban <user_id>`", parse_mode="MarkdownV2"
        )
        return
    try:
        target = int(context.args[0])
        await db.unban_user(target)
        await update.message.reply_text(
            f"✅ کاربر `{target}` رفع مسدودی شد\\.", parse_mode="MarkdownV2"
        )
        logger.info("Admin {} unbanned user {}", update.effective_user.id, target)
    except ValueError:
        await update.message.reply_text("❌ شناسه کاربر باید عدد باشد\\.", parse_mode="MarkdownV2")


# ─── /userinfo ────────────────────────────────────────────────────────────────

@admin_only
async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "⚠️ استفاده: `/userinfo <user_id>`", parse_mode="MarkdownV2"
        )
        return
    try:
        target = int(context.args[0])
        info = await db.get_user_info(target)
        if not info:
            await update.message.reply_text(
                "❌ کاربری با این شناسه یافت نشد\\.", parse_mode="MarkdownV2"
            )
            return
        await update.message.reply_text(
            MSG_USER_INFO.format(
                user_id=info["user_id"],
                first_name=escape_md(info["first_name"]),
                username=escape_md(info["username"]),
                join_date=escape_md(info["join_date"]),
                download_count=info["download_count"],
                banned=escape_md(info["banned"]),
            ),
            parse_mode="MarkdownV2",
        )
    except ValueError:
        await update.message.reply_text("❌ شناسه کاربر باید عدد باشد\\.", parse_mode="MarkdownV2")


# ─── Handler list ─────────────────────────────────────────────────────────────

admin_handlers = [
    CommandHandler("stats", cmd_stats),
    CommandHandler("broadcast", cmd_broadcast),
    CommandHandler("ban", cmd_ban),
    CommandHandler("unban", cmd_unban),
    CommandHandler("userinfo", cmd_userinfo),
]
