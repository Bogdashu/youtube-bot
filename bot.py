import os
import re
import tempfile
import subprocess
import shutil
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.environ.get("BOT_TOKEN")

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 Отправь YouTube ссылку — я скачаю видео")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    msg = await update.message.reply_text("⏳ Начинаю загрузку...")

    tmpdir = tempfile.mkdtemp(prefix="yt_")
    outtmpl = os.path.join(tmpdir, "video.mp4")

    cmd = [
        "python", "-m", "yt_dlp",
        "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "-N", "8",
        "--merge-output-format", "mp4",
        "-o", outtmpl,
        url
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        for line in process.stderr:
            percent = parse_progress(line)
            if percent is not None:
                try:
                    await msg.edit_text(f"⏳ Скачивание... {percent:.1f}%")
                except:
                    pass

        process.wait()

        if process.returncode != 0:
            await msg.edit_text("❌ Ошибка при скачивании")
            return

        if not os.path.exists(outtmpl) or os.path.getsize(outtmpl) == 0:
            await msg.edit_text("❌ Файл не найден или пустой")
            return

        file_size = os.path.getsize(outtmpl) / (1024 * 1024)
        await msg.edit_text(f"📤 Отправляю видео... ({file_size:.1f}MB)")

        with open(outtmpl, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption="✅ Готово!"
            )

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    if not TOKEN:
        print("❌ Токен не найден!")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot started on Railway...")
    app.run_polling()


if __name__ == "__main__":
    main()
