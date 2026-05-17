import os
import re
import tempfile
import subprocess
import shutil

from telegram import Update, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# =======================
# Railway ENV
# =======================
TOKEN = os.getenv("BOT_TOKEN")

progress_regex = re.compile(r'(\d{1,3}(?:\.\d+)?)%')


def parse_progress(line):
    match = progress_regex.search(line)
    if match:
        return float(match.group(1))
    return None


# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку — я скачаю видео"
    )


# ---------------- DOWNLOAD ----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    msg = await update.message.reply_text(
        "⏳ Скачиваю видео..."
    )

    tmpdir = tempfile.mkdtemp(prefix="yt_")
    outtmpl = os.path.join(tmpdir, "video.%(ext)s")

    # =======================
    # yt-dlp command
    # =======================
    cmd = [
        "python", "-m", "yt_dlp",

        # не качать плейлист
        "--no-playlist",

        # mp4 со звуком
        "-f",
        "best[ext=mp4][height<=1080]/best[height<=1080]",

        # 8 потоков
        "-N", "8",

        # стабильность
        "--socket-timeout", "60",
        "--retries", "10",

        # merge
        "--merge-output-format", "mp4",

        # progress
        "--newline",

        "-o", outtmpl,
        url
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    downloaded_file = None

    try:
        # =======================
        # progress
        # =======================
        for line in process.stderr:
            percent = parse_progress(line)

            if percent is not None:
                try:
                    await msg.edit_text(
                        f"⏳ Скачивание... {percent:.1f}%"
                    )
                except:
                    pass

        process.wait()

        # =======================
        # find file
        # =======================
        for f in os.listdir(tmpdir):
            if f.endswith(".mp4"):
                downloaded_file = os.path.join(tmpdir, f)
                break

        if not downloaded_file:
            await msg.edit_text(
                "❌ Ошибка: файл не найден"
            )
            return

        # =======================
        # size
        # =======================
        size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)

        await msg.edit_text(
            f"📤 Отправляю видео... ({size_mb:.1f}MB)"
        )

        # =======================
        # send video
        # =======================
        with open(downloaded_file, "rb") as video_file:
            await update.message.reply_video(
                video=InputFile(video_file),
                caption="✅ Готово!",

                # Railway fix
                read_timeout=120,
                write_timeout=120,
                connect_timeout=120,
                pool_timeout=120,

                supports_streaming=True
            )

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------- MAIN ----------------
def main():
    if not TOKEN:
        print("❌ BOT_TOKEN не найден")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))

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
