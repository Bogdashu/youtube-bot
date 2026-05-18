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


def run_cmd(cmd: list[str]) -> tuple[int, str]:
    """
    Запускает команду и возвращает:
    (returncode, combined_output)
    """
    p = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return p.returncode, p.stdout or ""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 YouTube Downloader\n\n"
        "✅ 1080p или 720p\n"
        "✅ Предпросмотр видео\n"
        "✅ До 2GB через local Bot API\n\n"
        "📩 Отправь YouTube-ссылку"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = (update.message.text or "").strip()

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Отправь YouTube ссылку")
        return

    msg = await update.message.reply_text("🔍 Получаю информацию о видео...")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Название
        title = "YouTube Video"
        try:
            code, out = run_cmd([
                "yt-dlp",
                "--no-playlist",
                "--print",
                "%(title)s",
                url,
            ])
            if code == 0 and out.strip():
                title = out.strip().splitlines()[-1].strip()
        except Exception as e:
            print("TITLE ERROR:", e)

        await msg.edit_text(
            f"🎬 {title}\n\n"
            f"⏳ Начинаю скачивание..."
        )

        outtmpl = os.path.join(tmpdir, "video.%(ext)s")

        # Только 1080p или 720p.
        # Если ни того, ни другого нет — не скатываемся в 480p.
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--newline",
            "--merge-output-format", "mp4",
            "--extractor-args", "youtube:player_client=android",
            "-f",
            (
                "bv*[height=1080]+ba/"
                "b[height=1080]/"
                "bv*[height=720]+ba/"
                "b[height=720]"
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
        log_lines = []

        for line in process.stdout:
            line = line.rstrip()
            if line:
                log_lines.append(line)
                if len(log_lines) > 40:
                    log_lines.pop(0)

            percent = parse_progress(line)
            if percent is not None:
                now = time.time()
                if now - last_update >= 5:
                    try:
                        filled = int(percent // 10)
                        progress_bar = "🟩" * filled + "⬜" * (10 - filled)

                        await msg.edit_text(
                            f"📥 Скачивание видео...\n\n"
                            f"🎬 {title}\n\n"
                            f"{progress_bar}\n"
                            f"⏳ {percent:.1f}%"
                        )
                        last_update = now
                    except Exception as e:
                        print("EDIT ERROR:", e)

        process.wait()

        if process.returncode != 0:
            print("YT-DLP FAILED LOG:")
            for x in log_lines:
                print(x)

            error_text = "❌ Ошибка скачивания видео.\n\n"
            if log_lines:
                # показываем последнюю строку ошибки, чтобы было понятно, что именно сломалось
                error_text += f"Последняя ошибка:\n`{log_lines[-1]}`"
            else:
                error_text += "Смотри логи Railway."

            await msg.edit_text(error_text)
            return

        video_file = None
        for f in os.listdir(tmpdir):
            if f.endswith((".mp4", ".mkv", ".webm")):
                video_file = os.path.join(tmpdir, f)
                break

        if not video_file:
            await msg.edit_text("❌ Видео не найдено")
            return

        real_size = os.path.getsize(video_file) / (1024 * 1024)
        size_text = f"{real_size / 1024:.2f} GB" if real_size > 1024 else f"{real_size:.1f} MB"

        # Реальное качество скачанного файла
        quality = "Unknown"
        try:
            quality_cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=height",
                "-of", "csv=p=0",
                video_file,
            ]
            height = subprocess.check_output(quality_cmd, text=True).strip()
            if height:
                quality = f"{height}p"
        except Exception as e:
            print("FFPROBE ERROR:", e)

        await msg.edit_text(
            f"📤 Отправка видео...\n\n"
            f"🎬 {title}\n\n"
            f"📺 Качество: {quality}\n"
            f"📦 Размер: {size_text}"
        )

        with open(video_file, "rb") as v:
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
    if not TOKEN:
        print("❌ BOT_TOKEN отсутствует")
        return

    if not LOCAL_BOT_API_URL:
        print("❌ LOCAL_BOT_API_URL отсутствует")
        return

    app = (
        Application.builder()
        .token(TOKEN)
        .base_url(f"{LOCAL_BOT_API_URL}/bot")
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 BOT STARTED")
    app.run_polling()


if __name__ == "__main__":
    main()
