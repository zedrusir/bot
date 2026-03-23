from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from bot.utils.messages import MSG_ERROR_GENERAL


async def handle_error(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Global error handler — logs every unhandled exception."""
    logger.error(
        "Unhandled exception (update={}): {}",
        update,
        context.error,
        exc_info=context.error,
    )
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                MSG_ERROR_GENERAL, parse_mode="MarkdownV2"
            )
        except Exception:
            pass
