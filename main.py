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

# ВАЖНО
LOCAL_BOT_API_URL = os.getenv(
    "LOCAL_BOT_API_URL",
    "http://telegram-bot-api:8081"
)

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def parse_progress(line: str):
    match = progress_regex.search(line)
    return float(match.group(1)) if match else None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь ссылку YouTube\n"
        "✅ 1080p\n"
        "✅ До 2GB\n"
        "✅ Без лимита 50MB"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Отправь YouTube ссылку")
        return

    msg = await update.message.reply_text("⏳ Скачивание...")

    with tempfile.TemporaryDirectory() as tmpdir:

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

        for line in process.stdout:
            percent = parse_progress(line)

            if percent is not None:
                try:
                    await msg.edit_text(
                        f"⏳ Скачивание: {percent:.1f}%"
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
            await msg.edit_text("❌ Ошибка скачивания")
            return

        size_mb = os.path.getsize(video_file) / (1024 * 1024)

        await msg.edit_text(
            f"📤 Отправка файла ({size_mb:.1f} MB)"
        )

        with open(video_file, "rb") as v:

            # ВАЖНО:
            # reply_document = до 2GB через local bot api
            await update.message.reply_document(
                document=v,
                filename=os.path.basename(video_file),
                caption=(
                    f"✅ Готово\n"
                    f"📦 Размер: {size_mb:.1f} MB"
                ),
                read_timeout=1200,
                write_timeout=1200,
            )

        await msg.delete()


def main():

    if not TOKEN:
        print("❌ BOT_TOKEN отсутствует")
        return

    # ВОТ ГЛАВНОЕ ИСПРАВЛЕНИЕ
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
            handle_message,
        )
    )

    print("🤖 Бот запущен")

    app.run_polling()


if __name__ == "__main__":
    main()
