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

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def parse_progress(line: str):
    match = progress_regex.search(line)
    return float(match.group(1)) if match else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🎬 YouTube Downloader\n\n"
        "✅ 1080p качество\n"
        "✅ До 2GB\n"
        "✅ Видео с предпросмотром\n\n"
        "📩 Отправь ссылку YouTube"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = update.message.text.strip()

    if "youtube.com" not in url and "youtu.be" not in url:

        await update.message.reply_text(
            "❌ Отправь YouTube ссылку"
        )

        return

    msg = await update.message.reply_text(
        "🔍 Получаю информацию о видео..."
    )

    with tempfile.TemporaryDirectory() as tmpdir:

        # Получаем название
        try:

            title = subprocess.check_output(
                [
                    "yt-dlp",
                    "--print",
                    "%(title)s",
                    url
                ],
                text=True
            ).strip()

        except:

            title = "YouTube Video"

        # ВСЕГДА 1080p
        quality = "1080p"

        await msg.edit_text(
            f"🎬 {title}\n\n"
            f"📺 Качество: {quality}\n\n"
            f"⏳ Начинаю скачивание..."
        )

        outtmpl = os.path.join(tmpdir, "video.%(ext)s")

        cmd = [
            "yt-dlp",
            "-f",
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "--merge-output-format",
            "mp4",
            "--newline",
            "--extractor-args",
            "youtube:player_client=android",
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

        for line in process.stdout:

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
                            f"📺 Качество: {quality}\n\n"
                            f"{progress_bar}\n"
                            f"⏳ {percent:.1f}%"
                        )

                        last_update = now

                    except Exception as e:
                        print(e)

        process.wait()

        if process.returncode != 0:

            await msg.edit_text(
                "❌ Ошибка скачивания"
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

        real_size = os.path.getsize(video_file) / (1024 * 1024)

        if real_size > 1024:

            size_text = f"{real_size / 1024:.2f} GB"

        else:

            size_text = f"{real_size:.1f} MB"

        await msg.edit_text(
            f"📤 Отправка видео...\n\n"
            f"🎬 {title}\n\n"
            f"📺 Качество: {quality}\n"
            f"📦 Размер: {size_text}"
        )

        with open(video_file, "rb") as v:

            # ВАЖНО
            # reply_video = preview
            await update.message.reply_video(
                video=v,
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

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
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
