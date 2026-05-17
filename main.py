import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from yt_dlp import YoutubeDL

TOKEN = os.getenv("BOT_TOKEN")

MAX_SIZE_1080 = 50 * 1024 * 1024


# =========================
# START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку"
    )


# =========================
# CHECK URL
# =========================
def is_youtube(url: str):
    return (
        "youtube.com" in url
        or "youtu.be" in url
    )


# =========================
# FIND VIDEO
# =========================
def find_video(folder):
    exts = [".mp4", ".mkv", ".webm"]

    files = []

    for p in Path(folder).rglob("*"):
        if p.is_file():
            if p.suffix.lower() in exts:
                files.append(p)

    if not files:
        return None

    return str(max(files, key=lambda x: x.stat().st_size))


# =========================
# DOWNLOAD
# =========================
def download_video(url, folder, quality):
    outtmpl = os.path.join(
        folder,
        "%(title)s.%(ext)s"
    )

    ydl_opts = {
        # Видео + звук
        "format": (
            f"bv*[height<={quality}]+ba/"
            f"b[height<={quality}]/"
            "best"
        ),

        # Склейка
        "merge_output_format": "mp4",

        "outtmpl": outtmpl,

        "quiet": True,
        "noplaylist": True,

        # Стабильность
        "retries": 15,
        "fragment_retries": 15,
        "extractor_retries": 15,

        "socket_timeout": 30,

        # ffmpeg mp4
        "postprocessors": [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
    }

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    return find_video(folder)


# =========================
# HANDLE
# =========================
async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    url = update.message.text.strip()

    if not is_youtube(url):
        await update.message.reply_text(
            "❌ Это не YouTube ссылка"
        )
        return

    msg = await update.message.reply_text(
        "⏳ Скачиваю..."
    )

    tmpdir = tempfile.mkdtemp(prefix="yt_")

    try:
        # =========================
        # TRY 1080P
        # =========================
        await msg.edit_text(
            "⏳ Пробую 1080p..."
        )

        video = await asyncio.to_thread(
            download_video,
            url,
            tmpdir,
            1080
        )

        if not video:
            raise Exception("Видео не найдено")

        size = os.path.getsize(video)

        # =========================
        # IF > 50MB => 720P
        # =========================
        if size > MAX_SIZE_1080:
            shutil.rmtree(tmpdir, ignore_errors=True)

            tmpdir = tempfile.mkdtemp(prefix="yt_")

            await msg.edit_text(
                "⚠️ Видео >50MB\n⏳ Перехожу на 720p..."
            )

            video = await asyncio.to_thread(
                download_video,
                url,
                tmpdir,
                720
            )

            if not video:
                raise Exception("720p видео не найдено")

        # =========================
        # SEND
        # =========================
        await msg.edit_text(
            "📤 Отправляю..."
        )

        with open(video, "rb") as f:
            await update.message.reply_video(
                video=f,
                caption="✅ Готово",
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600,
            )

        await msg.delete()

    except Exception as e:
        await msg.edit_text(
            f"❌ Ошибка:\n{e}"
        )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# =========================
# MAIN
# =========================
def main():
    if not TOKEN:
        print("BOT_TOKEN not found")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    print("BOT STARTED")

    app.run_polling()


if __name__ == "__main__":
    main()
