import time

from loguru import logger
from telegram import Message
from telegram.error import BadRequest, RetryAfter


class ProgressUpdater:
    """
    Wraps a Telegram Message and throttles edits to at most one per
    `min_interval` seconds, preventing Telegram's 429 rate-limit errors.
    """

    def __init__(self, message: Message, min_interval: float = 4.0):
        self.message = message
        self.min_interval = min_interval
        self._last_update: float = 0.0
        self._last_text: str = ""

    async def update(self, text: str, force: bool = False) -> None:
        now = time.monotonic()
        if text == self._last_text:
            return
        if not force and (now - self._last_update) < self.min_interval:
            return
        try:
            await self.message.edit_text(
                text,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True,
            )
            self._last_text = text
            self._last_update = now
        except RetryAfter:
            # Telegram rate-limit — skip this update, next one will go through
            pass
        except BadRequest as exc:
            # "Message is not modified" is expected; anything else is a real error
            if "message is not modified" not in str(exc).lower():
                logger.warning("ProgressUpdater: unexpected BadRequest: {}", exc)
        except Exception as exc:
            logger.warning("ProgressUpdater: failed to edit message: {}", exc)

    async def force_update(self, text: str) -> None:
        await self.update(text, force=True)


def make_progress_bar(percent: float, length: int = 15) -> str:
    """Return a Unicode block progress bar string, e.g. [████████░░░░░░░] 53.3%"""
    percent = max(0.0, min(100.0, percent))
    filled = round(length * percent / 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"`[{bar}] {percent:.1f}%`"
