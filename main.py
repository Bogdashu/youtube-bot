import os
import re
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


def parse_progress(line):
    match = progress_regex.search(line)
    return float(match.group(1)) if match else None


async def start(update: Update, context):

    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку"
    )


async def handle_message(update: Update, context):

    url = update.message.text.strip()

    if "youtube.com" not in url and "youtu.be" not in url:

        await update.message.reply_text(
            "❌ Это не YouTube ссылка"
        )
        return

    msg = await update.message.reply_text(
        "📥 Скачивание видео...\n\n⏳ 0%"
    )

    with tempfile.TemporaryDirectory() as tmpdir:

        outtmpl = os.path.join(tmpdir, "video.%(ext)s")

        cmd = [
            "yt-dlp",
            "-f",
            "bestvideo[height<=1080]+bestaudio/best",
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
        )

        last_percent = -5

        for line in process.stdout:

            percent = parse_progress(line)

            if percent is not None:

                if percent - last_percent >= 5:

                    last_percent = percent

                    try:

                        await msg.edit_text(
                            f"📥 Скачивание видео...\n\n"
                            f"⏳ {percent:.1f}%"
                        )

                    except:
                        pass

        process.wait()

        video_file = None

        for f in os.listdir(tmpdir):

            if f.endswith((".mp4", ".mkv", ".webm")):

                video_file = os.path.join(tmpdir, f)
                break

        if not video_file:

            await msg.edit_text(
                "❌ Ошибка скачивания"
            )
            return

        size_mb = os.path.getsize(video_file)/(1024*1024)

        await msg.edit_text(
            f"📤 Отправка видео...\n\n"
            f"📦 {size_mb:.1f} MB"
        )

        with open(video_file,"rb") as v:

            await update.message.reply_video(
                video=v,
                caption="✅ Готово",
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
        .base_file_url(f"{LOCAL_BOT_API_URL}/file/bot")
        .local_mode(True)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    print("BOT STARTED")

    app.run_polling()


if __name__ == "__main__":
    main()
