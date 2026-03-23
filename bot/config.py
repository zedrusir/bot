import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (one level above this file's directory)
load_dotenv(Path(__file__).parent.parent / ".env")

BASE_DIR = Path(__file__).parent.parent


class Config:
    # ── Telegram ──────────────────────────────────────────────
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    ADMIN_IDS: list[int] = [
        int(x.strip())
        for x in os.getenv("ADMIN_IDS", "1633424988").split(",")
        if x.strip()
    ]

    # ── Google Drive ──────────────────────────────────────────
    SERVICE_ACCOUNT_PATH: str = os.getenv(
        "SERVICE_ACCOUNT_PATH",
        str(BASE_DIR / "credentials" / "service_account.json"),
    )
    DRIVE_FOLDER_ID: str = os.getenv("DRIVE_FOLDER_ID", "")

    # ── Paths ─────────────────────────────────────────────────
    DOWNLOAD_DIR: Path = BASE_DIR / "data" / "downloads"
    DB_PATH: str = str(BASE_DIR / "data" / "bot.db")
    LOG_DIR: Path = BASE_DIR / "logs"
    LOG_FILE: str = str(BASE_DIR / "logs" / "bot.log")

    # ── Proxy (optional — for servers that can't reach Telegram directly)
    # Format: socks5://user:pass@host:port  or  http://user:pass@host:port
    PROXY_URL: str = os.getenv("PROXY_URL", "")

    # ── Rate limiting ─────────────────────────────────────────
    MAX_CONCURRENT_PER_USER: int = int(os.getenv("MAX_CONCURRENT_PER_USER", "3"))

    def validate(self) -> None:
        missing = []
        if not self.BOT_TOKEN:
            missing.append("BOT_TOKEN")
        if not self.DRIVE_FOLDER_ID:
            missing.append("DRIVE_FOLDER_ID")
        if not Path(self.SERVICE_ACCOUNT_PATH).exists():
            missing.append(f"SERVICE_ACCOUNT_PATH ({self.SERVICE_ACCOUNT_PATH} not found)")
        if missing:
            raise RuntimeError(
                f"Missing required configuration: {', '.join(missing)}\n"
                "Copy .env.example → .env and fill in the values."
            )


config = Config()
