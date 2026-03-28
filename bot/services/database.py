import aiosqlite
from loguru import logger
from bot.config import config


class Database:
    """Thin async wrapper around SQLite for user and download tracking."""

    def __init__(self) -> None:
        self.path = config.DB_PATH

    # ─── Setup ────────────────────────────────────────────────────────────────

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id       INTEGER PRIMARY KEY,
                    username      TEXT    DEFAULT '',
                    first_name    TEXT    DEFAULT '',
                    join_date     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_banned     INTEGER DEFAULT 0,
                    download_count INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    url          TEXT    NOT NULL,
                    title        TEXT    DEFAULT '',
                    quality      TEXT    DEFAULT '',
                    file_size_mb REAL    DEFAULT 0,
                    drive_id     TEXT    DEFAULT '',
                    drive_link   TEXT    DEFAULT '',
                    status       TEXT    DEFAULT 'pending',
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            # Indexes for frequently queried columns
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_downloads_user_id "
                "ON downloads(user_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_downloads_status "
                "ON downloads(status)"
            )
            await db.execute(
                # Covers history queries: WHERE user_id = ? AND status = 'done' ORDER BY created_at DESC
                "CREATE INDEX IF NOT EXISTS idx_downloads_user_status_time "
                "ON downloads(user_id, status, created_at DESC)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_is_banned "
                "ON users(is_banned)"
            )
            await db.commit()
        logger.info("Database ready at {}", self.path)

    # ─── Users ────────────────────────────────────────────────────────────────

    async def add_or_update_user(
        self, user_id: int, username: str, first_name: str
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username, first_name)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username   = excluded.username,
                    first_name = excluded.first_name
                """,
                (user_id, username, first_name),
            )
            await db.commit()

    async def is_banned(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT is_banned FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return bool(row and row[0])

    async def ban_user(self, user_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,)
            )
            await db.commit()

    async def unban_user(self, user_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,)
            )
            await db.commit()

    async def get_all_user_ids(self) -> list[int]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT user_id FROM users WHERE is_banned = 0"
            ) as cur:
                rows = await cur.fetchall()
                return [r[0] for r in rows]

    # ─── Downloads ────────────────────────────────────────────────────────────

    async def add_download(
        self, user_id: int, url: str, title: str, quality: str
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """
                INSERT INTO downloads (user_id, url, title, quality, status)
                VALUES (?, ?, ?, ?, 'downloading')
                """,
                (user_id, url, title, quality),
            )
            await db.commit()
            return cur.lastrowid  # type: ignore[return-value]

    async def update_download(self, download_id: int, **kwargs) -> None:
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [download_id]
        async with aiosqlite.connect(self.path) as db:
            # Check if we should increment download_count BEFORE applying the update,
            # so we only count transitions into 'done' (not duplicate calls with status='done')
            should_increment_for: int | None = None
            if kwargs.get("status") == "done":
                async with db.execute(
                    "SELECT user_id FROM downloads WHERE id = ? AND status != 'done'",
                    (download_id,),
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        should_increment_for = row[0]

            await db.execute(
                f"UPDATE downloads SET {set_clause} WHERE id = ?", values
            )

            if should_increment_for is not None:
                await db.execute(
                    "UPDATE users SET download_count = download_count + 1 WHERE user_id = ?",
                    (should_increment_for,),
                )

            await db.commit()

    async def get_user_history(self, user_id: int, limit: int = 5) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                """SELECT title, drive_link, file_size_mb, quality
                   FROM downloads
                   WHERE user_id = ? AND status = 'done'
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit),
            ) as cur:
                rows = await cur.fetchall()
        return [
            {
                "title": r[0] or "نامشخص",
                "link": r[1] or "",       # empty string for Telegram-only downloads
                "size": r[2] or 0.0,
                "quality": r[3] or "",
            }
            for r in rows
        ]

    async def get_user_info(self, user_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT user_id, username, first_name, join_date, is_banned, download_count "
                "FROM users WHERE user_id = ?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return {
            "user_id": row[0],
            "username": row[1] or "ندارد",
            "first_name": row[2] or "نامشخص",
            "join_date": str(row[3])[:10],
            "banned": "بله 🚫" if row[4] else "خیر ✅",
            "download_count": row[5],
        }

    # ─── Stats ────────────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE is_banned = 0"
            ) as cur:
                total_users = (await cur.fetchone())[0]  # type: ignore[index]

            async with db.execute(
                "SELECT COUNT(*) FROM downloads WHERE status = 'done'"
            ) as cur:
                total_downloads = (await cur.fetchone())[0]  # type: ignore[index]

            async with db.execute(
                "SELECT COALESCE(SUM(file_size_mb), 0) FROM downloads WHERE status = 'done'"
            ) as cur:
                total_mb = (await cur.fetchone())[0]  # type: ignore[index]

            async with db.execute(
                "SELECT COUNT(*) FROM downloads "
                "WHERE status = 'done' AND DATE(created_at) = DATE('now')"
            ) as cur:
                today = (await cur.fetchone())[0]  # type: ignore[index]

        return {
            "total_users": total_users,
            "total_downloads": total_downloads,
            "total_gb": float(total_mb) / 1024,
            "today_downloads": today,
        }


db = Database()
