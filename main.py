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
MAX_BEST_MB = 55
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
        # 1. Получаем инфу о размере
        # =========================
        probe_cmd = [
            "python",
            "-m",
            "yt_dlp",
            "--print",
            "%(filesize_approx)s",
            "-f",
            "bv*[height<=1080]+ba/best",
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
        # 2. Выбор качества
        # =========================
        if size_mb and size_mb <= MAX_BEST_MB:
            quality = "1080p"
            format_string = (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]"
            )
        else:
            quality = "720p"
            format_string = (
                "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]"
            )

        await msg.edit_text(
            f"⏳ Скачиваю видео... ({size_mb} MB)"
        )

        # =========================
        # 3. Скачивание
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

            text = line.decode("utf-8", errors="ignore").strip()

            percent = parse_progress(text)

            if percent is not None:
                current = int(percent)

                if current != last_percent:
                    last_percent = current

                    try:
                        await msg.edit_text(
                            f"⏳ Скачиваю видео... ({size_mb} MB)\n{current}%"
                        )
                    except:
                        pass

        await process.wait()

        # =========================
        # 4. Ищем mp4 файл
        # =========================
        downloaded_file = None

        for f in os.listdir(tmpdir):
            path = os.path.join(tmpdir, f)

            if (
                os.path.isfile(path)
                and f.lower().endswith(".mp4")
                and os.path.getsize(path) > 100000
            ):
                downloaded_file = path
                break

        if not downloaded_file:
            await msg.edit_text("❌ Ошибка: файл не найден")
            return

        # =========================
        # 5. Отправка
        # =========================
        await msg.edit_text(
            f"📤 Отправляю видео... ({quality})"
        )

        with open(downloaded_file, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption="✅ Готово!",
                read_timeout=300,
                write_timeout=300,
                connect_timeout=300,
                pool_timeout=300,
                supports_streaming=True,
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    print("🤖 Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
