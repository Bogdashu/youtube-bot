import asyncio
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from yt_dlp import YoutubeDL

# =======================
# TOKEN
# =======================
TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

# =======================
# SETTINGS
# =======================
MAX_SIZE_BYTES = 50 * 1024 * 1024
QUALITY_ORDER = (1080, 720)
ALLOWED_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ytbot")


def is_youtube_url(url: str) -> bool:
    url = url.lower()
    return any(
        part in url
        for part in (
            "youtube.com",
            "youtu.be",
            "youtube-nocookie.com",
            "m.youtube.com",
        )
    )


def find_ffmpeg_location() -> Optional[str]:
    """
    Возвращает путь к ffmpeg, если он доступен в PATH.
    Если ffmpeg лежит рядом с ботом или в отдельной папке, можно задать FFMPEG_PATH.
    """
    custom = os.getenv("FFMPEG_PATH")
    if custom:
        ffmpeg_bin = Path(custom)
        if ffmpeg_bin.is_file():
            return str(ffmpeg_bin.parent)
        if ffmpeg_bin.is_dir():
            return str(ffmpeg_bin)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return str(Path(ffmpeg).parent)

    return None


def check_ffmpeg_or_raise() -> str:
    ffmpeg_location = find_ffmpeg_location()
    if not ffmpeg_location:
        raise RuntimeError(
            "ffmpeg не найден. Установи ffmpeg и ffprobe или задай FFMPEG_PATH."
        )
    return ffmpeg_location


async def safe_edit_text(message, text: str) -> None:
    try:
        await message.edit_text(text)
    except Exception:
        pass


async def safe_delete_message(message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


class ProgressReporter:
    def __init__(self, loop: asyncio.AbstractEventLoop, message, label: str):
        self.loop = loop
        self.message = message
        self.label = label
        self.last_percent = -1.0
        self.last_update_ts = 0.0

    def hook(self, data: dict) -> None:
        status = data.get("status")

        if status == "downloading":
            downloaded = data.get("downloaded_bytes", 0) or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            percent = None

            if total and total > 0:
                percent = (downloaded / total) * 100.0

            now = time.monotonic()
            should_update = False

            if percent is not None:
                if (percent - self.last_percent) >= 5.0:
                    should_update = True
                if (now - self.last_update_ts) >= 7.0:
                    should_update = True
            else:
                if (now - self.last_update_ts) >= 10.0:
                    should_update = True

            if should_update:
                self.last_update_ts = now
                if percent is not None:
                    self.last_percent = percent
                    text = f"{self.label}\n{percent:.1f}%"
                else:
                    text = f"{self.label}\n⏳ Скачиваю..."

                asyncio.run_coroutine_threadsafe(
                    safe_edit_text(self.message, text),
                    self.loop,
                )

        elif status == "finished":
            asyncio.run_coroutine_threadsafe(
                safe_edit_text(
                    self.message,
                    f"{self.label}\n✅ Скачано, объединяю звук и видео...",
                ),
                self.loop,
            )


def build_ydl_opts(tmpdir: str, cap_height: int, progress_hook, ffmpeg_location: str) -> dict:
    # Всегда стараемся взять видео + аудио вместе.
    # bv*+ba = лучший видео+лучший аудио поток
    # /b      = fallback на прогрессивный файл, если он есть
    format_selector = (
        f"bv*[height<={cap_height}]+ba/"
        f"b[height<={cap_height}]/"
        f"bv*+ba/"
        f"b"
    )

    return {
        "format": format_selector,
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg_location,
        "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "continuedl": True,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 10,
        "extractor_retries": 10,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 20,
        "progress_hooks": [progress_hook],
        "keepvideo": False,
    }


def find_final_media_file(tmpdir: str) -> Optional[str]:
    root = Path(tmpdir)
    candidates = []

    for p in root.rglob("*"):
        if not p.is_file():
            continue

        name = p.name.lower()

        if any(
            name.endswith(suffix)
            for suffix in (
                ".part",
                ".ytdl",
                ".temp",
                ".info.json",
                ".json",
                ".jpg",
                ".jpeg",
                ".webp",
            )
        ):
            continue

        if p.suffix.lower() in ALLOWED_EXTS:
            candidates.append(p)

    if not candidates:
        for p in root.rglob("*"):
            if p.is_file():
                name = p.name.lower()
                if any(
                    name.endswith(suffix)
                    for suffix in (".part", ".ytdl", ".temp", ".info.json", ".json")
                ):
                    continue
                candidates.append(p)

    if not candidates:
        return None

    return str(max(candidates, key=lambda x: x.stat().st_size))


def download_once(url: str, tmpdir: str, cap_height: int, loop, progress_message, ffmpeg_location: str):
    reporter = ProgressReporter(
        loop=loop,
        message=progress_message,
        label=f"⏳ Скачиваю видео... (до {cap_height}p)",
    )

    opts = build_ydl_opts(tmpdir, cap_height, reporter.hook, ffmpeg_location)

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get("title") or "Видео"
        ydl.download([url])

    final_file = find_final_media_file(tmpdir)
    if not final_file or not os.path.exists(final_file):
        raise FileNotFoundError("Файл после скачивания не найден")

    return final_file, title


async def download_with_fallback(url: str, loop, progress_message, ffmpeg_location: str):
    last_error = None

    for cap in QUALITY_ORDER:
        tmpdir = tempfile.mkdtemp(prefix=f"yt_{cap}_")
        try:
            final_file, title = await asyncio.to_thread(
                download_once,
                url,
                tmpdir,
                cap,
                loop,
                progress_message,
                ffmpeg_location,
            )

            size_bytes = os.path.getsize(final_file)

            if cap == 1080 and size_bytes > MAX_SIZE_BYTES:
                await safe_edit_text(
                    progress_message,
                    "⚠️ 1080p вышло больше 50 MB, пробую 720p...",
                )
                shutil.rmtree(tmpdir, ignore_errors=True)
                continue

            return {
                "tmpdir": tmpdir,
                "file": final_file,
                "title": title,
                "size_bytes": size_bytes,
                "quality": cap,
            }

        except Exception as e:
            last_error = e
            shutil.rmtree(tmpdir, ignore_errors=True)
            logger.exception("Download failed for cap=%s", cap)

    if last_error:
        raise last_error

    raise RuntimeError("Не удалось скачать видео")


# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 Отправь YouTube ссылку — я скачаю видео")


# ---------------- DOWNLOAD ----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = (update.message.text or "").strip()

    if not is_youtube_url(url):
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    status_message = await update.message.reply_text("⏳ Подготавливаю скачивание...")
    loop = asyncio.get_running_loop()
    tmpdir_to_cleanup = None

    try:
        ffmpeg_location = check_ffmpeg_or_raise()

        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.UPLOAD_VIDEO,
        )

        result = await download_with_fallback(
            url,
            loop,
            status_message,
            ffmpeg_location,
        )

        tmpdir_to_cleanup = result["tmpdir"]
        downloaded_file = result["file"]
        title = result["title"]
        quality = result["quality"]
        size_mb = result["size_bytes"] / (1024 * 1024)

        await safe_edit_text(
            status_message,
            f"📤 Отправляю видео... ({quality}p)",
        )

        caption = f"✅ Готово!\n{title}\n{quality}p • {size_mb:.1f} MB"

        try:
            with open(downloaded_file, "rb") as video:
                await update.message.reply_video(
                    video=video,
                    caption=caption,
                    supports_streaming=True,
                    read_timeout=600,
                    write_timeout=600,
                    connect_timeout=60,
                    pool_timeout=60,
                )
        except Exception:
            with open(downloaded_file, "rb") as video:
                await update.message.reply_document(
                    document=video,
                    caption=caption,
                    read_timeout=600,
                    write_timeout=600,
                    connect_timeout=60,
                    pool_timeout=60,
                )

        await safe_delete_message(status_message)

    except Exception as e:
        await safe_edit_text(status_message, f"❌ Ошибка: {e}")

    finally:
        if tmpdir_to_cleanup:
            shutil.rmtree(tmpdir_to_cleanup, ignore_errors=True)


# ---------------- MAIN ----------------
def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Вставь токен!")
        return

    try:
        check_ffmpeg_or_raise()
    except Exception as e:
        print(f"❌ {e}")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    print("🤖 Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
