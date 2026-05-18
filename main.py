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
        "🎬 YouTube Downloader Bot\n\n"
        "✅ Скачать видео 1080p\n"
        "✅ Поддержка файлов до 2GB\n"
        "✅ Быстрая загрузка\n\n"
        "📩 Просто отправь ссылку YouTube"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = update.message.text.strip()

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text(
            "❌ Это не YouTube ссылка"
        )
        return

    msg = await update.message.reply_text(
        "🔍 Получаю информацию о видео..."
    )

    with tempfile.TemporaryDirectory() as tmpdir:

        # Получение информации о видео
        info_cmd = [
            "yt-dlp",
            "--print",
            "%(title)s|||%(resolution)s|||%(filesize_approx)s",
            url,
        ]

        try:

            info_result = subprocess.check_output(
                info_cmd,
                text=True,
                stderr=subprocess.DEVNULL
            ).strip()

            parts = info_result.split("|||")

            title = parts[0] if len(parts) > 0 else "Unknown"
            quality = parts[1] if len(parts) > 1 else "1080p"

            filesize = "Неизвестно"

            if len(parts) > 2 and parts[2].isdigit():

                size_mb = int(parts[2]) / (1024 * 1024)

                if size_mb > 1024:
                    filesize = f"{size_mb / 1024:.2f} GB"
                else:
                    filesize = f"{size_mb:.1f} MB"

        except:
            title = "YouTube Video"
            quality = "1080p"
            filesize = "Неизвестно"

        await msg.edit_text(
            f"🎬 Название:\n{title}\n\n"
            f"📺 Качество: {quality}\n"
            f"📦 Размер: {filesize}\n\n"
            f"⏳ Подготовка к скачиванию..."
        )

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
            bufsize=1,
        )

        last_update = 0
        last_percent = 0

        for line in process.stdout:

            percent = parse_progress(line)

            if percent is not None:

                last_percent = percent

                now = time.time()

                # обновляем максимум раз в 5 секунд
                if now - last_update >= 5:

                    try:

                        progress_bar = "▓" * int(percent // 10)
                        progress_bar += "░" * (10 - int(percent // 10))

                        await msg.edit_text(
                            f"📥 Скачивание видео...\n\n"
                            f"🎬 {title}\n\n"
                            f"📺 Качество: {quality}\n"
                            f"📦 Размер: {filesize}\n\n"
                            f"[{progress_bar}] {percent:.1f}%"
                        )

                        last_update = now

                    except Exception as e:
                        print(e)

        process.wait()

        if process.returncode != 0:

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

        real_size = os.path.getsize(video_file) / (1024 * 1024)

        if real_size > 1024:
            real_size_text = f"{real_size / 1024:.2f} GB"
        else:
            real_size_text = f"{real_size:.1f} MB"

        await msg.edit_text(
            f"📤 Отправка видео...\n\n"
            f"🎬 {title}\n\n"
            f"📺 Качество: {quality}\n"
            f"📦 Размер: {real_size_text}"
        )

        with open(video_file, "rb") as v:

            await update.message.reply_document(
                document=v,
                filename=f"{title}.mp4",
                caption=(
                    f"✅ Видео успешно скачано\n\n"
                    f"🎬 {title}\n"
                    f"📺 Качество: {quality}\n"
                    f"📦 Размер: {real_size_text}"
                ),
                read_timeout=1200,
                write_timeout=1200,
            )

        await msg.delete()


def main():

    if not TOKEN:

        print("BOT_TOKEN отсутствует")

        return

    if not LOCAL_BOT_API_URL:

        print("LOCAL_BOT_API_URL отсутствует")

        return

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
            handle_message,
        )
    )

    print("BOT STARTED")

    app.run_polling()


if __name__ == "__main__":
    main()
