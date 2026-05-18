import os
import re
import time
import tempfile
import subprocess

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL")
COOKIES_TEXT = os.getenv("YOUTUBE_COOKIES")

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def parse_progress(line: str):
    match = progress_regex.search(line)
    return float(match.group(1)) if match else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🎬 YouTube Downloader\n\n"
        "✅ Максимальное качество\n"
        "✅ 1080p / 720p\n"
        "✅ Предпросмотр видео\n"
        "✅ До 2GB\n\n"
        "📩 Отправь YouTube ссылку"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = (update.message.text or "").strip()

    if "youtube.com" not in url and "youtu.be" not in url:

        await update.message.reply_text(
            "❌ Отправь YouTube ссылку"
        )

        return

    msg = await update.message.reply_text(
        "🔍 Получаю информацию о видео..."
    )

    with tempfile.TemporaryDirectory() as tmpdir:

        cookies_path = os.path.join(tmpdir, "cookies.txt")

        with open(cookies_path, "w", encoding="utf-8") as f:
            f.write(COOKIES_TEXT)

        # Получаем title
        try:

            title = subprocess.check_output(
                [
                    "yt-dlp",

                    "--cookies",
                    cookies_path,

                    "--print",
                    "%(title)s",

                    url
                ],
                text=True,
                stderr=subprocess.DEVNULL
            ).strip()

        except:

            title = "YouTube Video"

        await msg.edit_text(
            f"🎬 {title}\n\n"
            f"⏳ Начинаю скачивание..."
        )

        outtmpl = os.path.join(tmpdir, "video.%(ext)s")

        cmd = [
            "yt-dlp",

            "--cookies",
            cookies_path,

            "--no-playlist",

            "--newline",

            "--merge-output-format",
            "mp4",

            "--extractor-args",
            "youtube:player_client=android,web",

            "-f",
            (
                "bv*[height<=1080]+ba/"
                "b[height<=1080]/"
                "bestvideo+bestaudio/"
                "best"
            ),

            "-o",
            outtmpl,

            url,
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        last_update = 0
        logs = []

        for line in process.stdout:

            logs.append(line)

            if len(logs) > 20:
                logs.pop(0)

            percent = parse_progress(line)

            if percent is not None:

                now = time.time()

                if now - last_update >= 5:

                    try:

                        filled = int(percent // 10)

                        progress_bar = (
                            "🟩" * filled
                            + "⬜" * (10 - filled)
                        )

                        await msg.edit_text(
                            f"📥 Скачивание видео...\n\n"
                            f"🎬 {title}\n\n"
                            f"{progress_bar}\n"
                            f"⏳ {percent:.1f}%"
                        )

                        last_update = now

                    except Exception as e:
                        print(e)

        process.wait()

        if process.returncode != 0:

            print("YT-DLP ERROR:")
            print("".join(logs))

            await msg.edit_text(
                "❌ Ошибка скачивания видео"
            )

            return

        video_file = None

        for f in os.listdir(tmpdir):

            if f.endswith((".mp4", ".mkv", ".webm")):

                video_file = os.path.join(tmpdir, f)

                break

        if not video_file:

            await msg.edit_text(
                "❌ Видео не найдено"
            )

            return

        # Размер файла
        real_size = os.path.getsize(video_file) / (1024 * 1024)

        if real_size > 1024:

            size_text = f"{real_size / 1024:.2f} GB"

        else:

            size_text = f"{real_size:.1f} MB"

        # Качество
        try:

            quality_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=height",
                "-of",
                "csv=p=0",
                video_file
            ]

            real_height = subprocess.check_output(
                quality_cmd,
                text=True
            ).strip()

            quality = f"{real_height}p"

        except:

            quality = "Unknown"

        await msg.edit_text(
            f"📤 Отправка видео...\n\n"
            f"🎬 {title}\n\n"
            f"📺 Качество: {quality}\n"
            f"📦 Размер: {size_text}"
        )

        with open(video_file, "rb") as v:

            await update.message.reply_video(
                video=v,

                filename=f"{title}.mp4",

                caption=(
                    f"🎬 {title}\n\n"
                    f"📺 Качество: {quality}\n"
                    f"📦 Размер: {size_text}"
                ),

                supports_streaming=True,

                read_timeout=1200,
                write_timeout=1200,
            )

        await msg.delete()


def main():

    app = (
        Application.builder()
        .token(TOKEN)
        .base_url(f"{LOCAL_BOT_API_URL}/bot")
        .build()
    )

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    print("🚀 BOT STARTED")

    app.run_polling()


if __name__ == "__main__":
    main()
