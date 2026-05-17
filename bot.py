import os
import re
import tempfile
import subprocess
import shutil
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

# Логирование для Railway
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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

    msg = await update.message.reply_text("⏳ Начинаю загрузку...")

    tmpdir = tempfile.mkdtemp(prefix="yt_")
    outtmpl = os.path.join(tmpdir, "video.%(ext)s")

    cmd = [
        "python", "-m", "yt_dlp",
        "-f", "bv*[height<=1080]+ba/best",
        "-N", "8",
        "--merge-output-format", "mp4",
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
        for line in process.stderr:
            percent = parse_progress(line)

            if percent is not None:
                try:
                    await msg.edit_text(f"⏳ Скачивание... {percent:.1f}%")
                except:
                    pass

        process.wait()

        for f in os.listdir(tmpdir):
            if f.endswith(".mp4"):
                downloaded_file = os.path.join(tmpdir, f)
                break

        if not downloaded_file:
            await msg.edit_text("❌ Ошибка: файл не найден")
            return

        await msg.edit_text("📤 Отправляю видео...")

        with open(downloaded_file, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption="✅ Готово!"
            )

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await msg.edit_text(f"❌ Ошибка: {e}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------- MAIN ----------------
def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Токен не найден! Установи переменную BOT_TOKEN в Railway")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot started on Railway...")
    logger.info("Бот успешно запущен на Railway")
    
    # Railway сам перезапускает при падениях
    app.run_polling()


if __name__ == "__main__":
    main()
