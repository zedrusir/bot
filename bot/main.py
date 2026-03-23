"""
Entry point.  Run with:  python -m bot.main
"""

from loguru import logger
from telegram.ext import Application

from bot.config import config
from bot.handlers.admin import admin_handlers
from bot.handlers.errors import handle_error
from bot.handlers.user import user_handlers
from bot.services.database import db


def _setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    logger.add(
        config.LOG_FILE,
        rotation="50 MB",
        retention="7 days",
        compression="zip",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
        enqueue=True,   # thread-safe
    )


async def _post_init(application: Application) -> None:  # type: ignore[type-arg]
    await db.init()
    logger.info("Bot started. Admin IDs: {}", config.ADMIN_IDS)


def main() -> None:
    _setup_logging()
    config.validate()   # fail fast if .env is incomplete

    logger.info("Building application…")

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    for handler in user_handlers + admin_handlers:
        app.add_handler(handler)

    app.add_error_handler(handle_error)

    logger.info("Polling started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
