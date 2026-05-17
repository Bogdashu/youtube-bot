import os
import re
import tempfile
import shutil
import asyncio

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# TOKEN
# =========================
TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

# =========================
# SETTINGS
# =========================
THREADS = "8"

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


# =========================
# HELPERS
# =========================
def parse_progress(line):
    match = progress_regex.search(line)
    if match:
        return float(match.group(1))
    return None


def format_mb(size_bytes):
    return round(size_bytes / 1024 / 1024, 1)


async def run_command(cmd):
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    output = []

    while True:
        line = await process.stdout.readline()

        if not line:
            break

        text = line.decode("utf-8", errors="ignore").strip()
        output.append(text)

    await process.wait()

    return process.returncode, output


# =========================
# START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку — я скачаю видео"
    )


# =========================
# DOWNLOAD
# =========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    msg = await update.message.reply_text("⏳ Скачиваю видео...")

    tmpdir = tempfile.mkdtemp(prefix="yt_")

    try:
        # =========================
        # Получаем размер видео
        # =========================
        probe_cmd = [
            "python",
            "-m",
            "yt_dlp",
            "--print",
            "%(filesize_approx)s",
            "-f",
            "bestvideo+bestaudio/best",
            url,
        ]

        code, probe_output = await run_command(probe_cmd)

        filesize = 0

        for line in probe_output:
            if line.isdigit():
                filesize = int(line)
                break

        size_mb = format_mb(filesize) if filesize else 0

        # =========================
        # Выбор качества
        # =========================
        if size_mb <= 100:
            quality = "1080p"

            format_string = (
                "bestvideo[height<=1080][ext=mp4]+"
                "bestaudio[ext=m4a]/best[height<=1080]"
            )

        elif size_mb <= 180:
            quality = "720p"

            format_string = (
                "bestvideo[height<=720][ext=mp4]+"
                "bestaudio[ext=m4a]/best[height<=720]"
            )

        else:
            quality = "480p"

            format_string = (
                "bestvideo[height<=480][ext=mp4]+"
                "bestaudio[ext=m4a]/best[height<=480]"
            )

        await msg.edit_text(
            f"⏳ Скачиваю видео... ({size_mb} MB)"
        )

        # =========================
        # Скачивание
        # =========================
        outtmpl = os.path.join(tmpdir, "%(title)s.%(ext)s")

        cmd = [
            "python",
            "-m",
            "yt_dlp",
            "-f",
            format_string,
            "-N",
            THREADS,
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "--newline",
            "-o",
            outtmpl,
            url,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        last_percent = -1

        while True:
            line = await process.stdout.readline()

            if not line:
                break

            text = line.decode(
                "utf-8",
                errors="ignore"
            ).strip()

            percent = parse_progress(text)

            if percent is not None:
                current = int(percent)

                if current != last_percent:
                    last_percent = current

                    try:
                        await msg.edit_text(
                            f"⏳ Скачиваю видео... ({size_mb} MB)\n"
                            f"{current}%"
                        )
                    except:
                        pass

        await process.wait()

        # =========================
        # Поиск готового видео
        # =========================
        downloaded_file = None

        video_extensions = (
            ".mp4",
            ".mkv",
            ".webm",
            ".mov"
        )

        for f in os.listdir(tmpdir):
            path = os.path.join(tmpdir, f)

            if (
                os.path.isfile(path)
                and f.lower().endswith(video_extensions)
                and os.path.getsize(path) > 100000
            ):
                downloaded_file = path
                break

        if not downloaded_file:
            files = os.listdir(tmpdir)

            await msg.edit_text(
                f"❌ Видео не найдено\n"
                f"Файлы: {files}"
            )
            return

        final_size = format_mb(
            os.path.getsize(downloaded_file)
        )

        # =========================
        # Слишком большой файл
        # =========================
        if final_size > 1900:
            await msg.edit_text(
                "❌ Видео слишком большое для Telegram"
            )
            return

        # =========================
        # Отправка
        # =========================
        await msg.edit_text(
            f"📤 Отправляю видео... ({quality})"
        )

        with open(downloaded_file, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=(
                    f"✅ Готово!\n"
                    f"📦 Размер: {final_size} MB"
                ),
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600,
                connect_timeout=600,
                pool_timeout=600,
            )

        await msg.delete()

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# =========================
# MAIN
# =========================
def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Вставь токен!")
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

    print("🤖 Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
