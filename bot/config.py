import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (one level above this file's directory)
load_dotenv(Path(__file__).parent.parent / ".env")

BASE_DIR = Path(__file__).parent.parent


VERSION = "1.5.0"


class Config:
    # ── Telegram ──────────────────────────────────────────────
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    ADMIN_IDS: list[int] = [
        int(x.strip())
        for x in os.getenv("ADMIN_IDS", "1633424988").split(",")
        if x.strip()
    ]

    # ── Google Drive ──────────────────────────────────────────
    OAUTH_CLIENT_PATH: str = os.getenv(
        "OAUTH_CLIENT_PATH",
        str(BASE_DIR / "credentials" / "oauth_client.json"),
    )
    OAUTH_TOKEN_PATH: str = os.getenv(
        "OAUTH_TOKEN_PATH",
        str(BASE_DIR / "credentials" / "token.json"),
    )
    DRIVE_FOLDER_ID: str = os.getenv("DRIVE_FOLDER_ID", "")

    # ── Paths ─────────────────────────────────────────────────
    DOWNLOAD_DIR: Path = BASE_DIR / "data" / "downloads"
    DB_PATH: str = str(BASE_DIR / "data" / "bot.db")
    LOG_DIR: Path = BASE_DIR / "logs"
    LOG_FILE: str = str(BASE_DIR / "logs" / "bot.log")

    # ── YouTube cookies (required for bot-detection bypass)
    # Export from browser using "Get cookies.txt LOCALLY" extension (Netscape format)
    YT_COOKIES_PATH: str = os.getenv(
        "YT_COOKIES_PATH",
        str(BASE_DIR / "credentials" / "cookies.txt"),
    )

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
        if not Path(self.OAUTH_CLIENT_PATH).exists():
            missing.append(f"OAUTH_CLIENT_PATH ({self.OAUTH_CLIENT_PATH} not found)")
        if not Path(self.OAUTH_TOKEN_PATH).exists():
            missing.append(
                f"OAUTH_TOKEN_PATH ({self.OAUTH_TOKEN_PATH} not found) — "
                "run: python setup_oauth.py"
            )
        if missing:
            raise RuntimeError(
                f"Missing required configuration: {', '.join(missing)}\n"
                "Copy .env.example → .env and fill in the values."
            )


config = Config()
