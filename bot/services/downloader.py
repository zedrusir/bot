import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Optional

import yt_dlp
from loguru import logger

from bot.config import config


def _base_ydl_opts() -> dict:
    """Base yt-dlp options shared by all calls."""
    opts: dict = {
        "extractor_args": {"youtube": {"player_client": ["web"]}},
        "remote_components": ["ejs:github"],
        "js_runtimes": {"node": {}},
    }
    p = Path(config.YT_COOKIES_PATH)
    if p.exists():
        opts["cookiefile"] = str(p)
    else:
        logger.warning("YT cookies file not found at {} — YouTube may block requests", p)
    if config.PROXY_URL:
        opts["proxy"] = config.PROXY_URL
    return opts

# ─── Quality format strings ───────────────────────────────────────────────────
def _vfmt(h: int) -> str:
    return (
        f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
        f"/bestvideo[height<={h}]+bestaudio"
        f"/best[height<={h}]/best"
    )


QUALITY_FORMATS: dict[str, str] = {
    # ── Video ──────────────────────────────────────────────────────────────
    "144p":  _vfmt(144),
    "240p":  _vfmt(240),
    "360p":  _vfmt(360),
    "480p":  _vfmt(480),
    "720p":  _vfmt(720),
    "1080p": _vfmt(1080),
    "1440p": _vfmt(1440),
    "4k":    _vfmt(2160),
    "best":  "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
    # ── Audio ──────────────────────────────────────────────────────────────
    "mp3_128": "bestaudio",
    "mp3_320": "bestaudio",
    "m4a":     "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
    "audio":   "bestaudio",       # legacy alias → mp3 192k
}

QUALITY_LABELS: dict[str, str] = {
    "144p":    "144p 📱",
    "240p":    "240p 📱",
    "360p":    "360p SD 📺",
    "480p":    "480p SD 📺",
    "720p":    "720p HD 🎬",
    "1080p":   "1080p Full HD 🎥",
    "1440p":   "1440p 2K 🖥",
    "4k":      "4K Ultra HD 🌟",
    "best":    "بهترین کیفیت ⭐",
    "mp3_128": "🎵 MP3 — 128 kbps",
    "mp3_320": "🎵 MP3 — 320 kbps",
    "m4a":     "🎶 M4A — بدون تبدیل",
    "audio":   "🎵 MP3 — 192 kbps",
}

_AUDIO_QUALITIES = {"mp3_128", "mp3_320", "m4a", "audio"}
_MP3_BITRATES = {"mp3_128": "128", "mp3_320": "320", "audio": "192"}

ProgressCallback = Callable[..., Awaitable[None]]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "نامشخص"
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_views(views: Optional[int]) -> str:
    if not views:
        return "نامشخص"
    if views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M"
    if views >= 1_000:
        return f"{views / 1_000:.1f}K"
    return str(views)


# ─── Public API ───────────────────────────────────────────────────────────────

async def get_video_info(url: str) -> dict:
    """Fetch video metadata *without* downloading anything."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, **_base_ydl_opts()}
    loop = asyncio.get_event_loop()

    def _fetch() -> dict:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)  # type: ignore[return-value]

    return await loop.run_in_executor(None, _fetch)


async def download_video(
    url: str,
    quality: str,
    progress_cb: Optional[ProgressCallback] = None,
) -> dict:
    """
    Download *url* at *quality* into a temporary directory.

    Returns a dict with:
        file_path    – absolute path to the merged mp4
        download_dir – directory that should be deleted after upload
        title        – video title string
        quality      – requested quality key
        size_mb      – file size in megabytes
    """
    download_dir = config.DOWNLOAD_DIR / str(uuid.uuid4())
    download_dir.mkdir(parents=True, exist_ok=True)

    fmt = QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"])
    is_audio = quality in _AUDIO_QUALITIES
    result: dict = {
        "file_path": None,
        "download_dir": str(download_dir),
        "title": "Unknown",
        "quality": quality,
        "size_mb": 0.0,
    }

    loop = asyncio.get_event_loop()

    def _progress_hook(d: dict) -> None:
        if d["status"] == "finished":
            result["file_path"] = d.get("filename")
        elif d["status"] == "downloading" and progress_cb:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed") or 0
            eta = d.get("eta") or 0
            percent = (downloaded / total * 100) if total else 0
            asyncio.run_coroutine_threadsafe(
                progress_cb(
                    percent=percent,
                    downloaded=downloaded,
                    total=total,
                    speed=speed,
                    eta=eta,
                ),
                loop,
            )

    if quality == "m4a":
        postprocessors = []
    elif quality in _MP3_BITRATES:
        postprocessors = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": _MP3_BITRATES[quality],
        }]
    elif is_audio:
        postprocessors = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    else:
        postprocessors = [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]

    ydl_opts = {
        "format": fmt,
        "outtmpl": str(download_dir / "%(title)s.%(ext)s"),
        "merge_output_format": None if is_audio else "mp4",
        "progress_hooks": [_progress_hook],
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        **_base_ydl_opts(),
        "postprocessors": postprocessors,
    }

    def _download() -> None:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            result["title"] = info.get("title", "Unknown")
            # yt-dlp may change the extension after merge; locate the file
            if not result["file_path"] or not Path(result["file_path"]).exists():
                mp4_files = list(download_dir.glob("*.mp4"))
                if mp4_files:
                    result["file_path"] = str(mp4_files[0])
                else:
                    all_files = [
                        f for f in download_dir.iterdir() if f.is_file()
                    ]
                    if all_files:
                        result["file_path"] = str(all_files[0])

    try:
        await loop.run_in_executor(None, _download)
    except Exception:
        shutil.rmtree(download_dir, ignore_errors=True)
        raise

    if result["file_path"] and Path(result["file_path"]).exists():
        result["size_mb"] = Path(result["file_path"]).stat().st_size / (1024 * 1024)
        logger.info(
            "Downloaded '{}' ({:.1f} MB) → {}",
            result["title"],
            result["size_mb"],
            result["file_path"],
        )
    else:
        shutil.rmtree(download_dir, ignore_errors=True)
        raise RuntimeError("Download completed but output file not found.")

    return result
