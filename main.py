import os
import re
import time
import tempfile
import subprocess

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN")
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL")
COOKIES_TEXT = os.getenv("YOUTUBE_COOKIES")

progress_re = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def progress(line: str):
    m = progress_re.search(line)
    return float(m.group(1)) if m else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 YouTube Downloader\n\n"
        "📌 1080p / 720p / best\n"
        "📌 стабильная загрузка\n"
        "📌 Railway ready\n\n"
        "Отправь ссылку"
    )


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = (update.message.text or "").strip()

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Нужна YouTube ссылка")
        return

    msg = await update.message.reply_text("🔍 анализ видео...")

    with tempfile.TemporaryDirectory() as tmp:

        cookies_path = os.path.join(tmp, "cookies.txt")
        if COOKIES_TEXT:
            with open(cookies_path, "w", encoding="utf-8") as f:
                f.write(COOKIES_TEXT)

        # TITLE
        try:
            title = subprocess.check_output([
                "yt-dlp",
                "--cookies", cookies_path,
                "--print", "%(title)s",
                url
            ], text=True).strip()
        except:
            title = "YouTube Video"

        await msg.edit_text(f"🎬 {title}\n\n⏳ скачивание...")

        out = os.path.join(tmp, "video.%(ext)s")

        cmd = [
            "yt-dlp",
            "--cookies", cookies_path,
            "--no-playlist",
            "--newline",
            "--merge-output-format", "mp4",

            # 🔥 FIXED extractor (важно)
            "--extractor-args",
            "youtube:player_client=web,android,mweb",

            # 🔥 STABLE FORMAT CHAIN
            "-f",
            "bv*[height<=1080]+ba/b[height<=1080]/bestvideo+bestaudio/best",

            "-o", out,
            url
        ]

        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        last = 0

        for line in p.stdout:
            pr = progress(line)

            if pr is not None and time.time() - last > 3:
                bar = "🟩" * int(pr // 10) + "⬜" * (10 - int(pr // 10))

                await msg.edit_text(
                    f"📥 скачивание...\n\n"
                    f"{title}\n\n"
                    f"{bar}\n{pr:.1f}%"
                )
                last = time.time()

        p.wait()

        if p.returncode != 0:
            await msg.edit_text("❌ ошибка скачивания")
            return

        file = None
        for f in os.listdir(tmp):
            if f.endswith((".mp4", ".mkv", ".webm")):
                file = os.path.join(tmp, f)
                break

        if not file:
            await msg.edit_text("❌ файл не найден")
            return

        size = os.path.getsize(file) / 1024 / 1024
        size_txt = f"{size/1024:.2f} GB" if size > 1024 else f"{size:.1f} MB"

        await msg.edit_text(
            f"📤 отправка...\n\n"
            f"🎬 {title}\n"
            f"📦 {size_txt}"
        )

        with open(file, "rb") as v:
            await update.message.reply_video(video=v, supports_streaming=True)

        await msg.delete()


def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .base_url(f"{LOCAL_BOT_API_URL}/bot")
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    print("BOT STARTED")
    app.run_polling()


if __name__ == "__main__":
    main()
