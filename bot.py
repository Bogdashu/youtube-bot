import os
import re
import tempfile
import subprocess
import shutil
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# =======================
# 🔥 ТОКЕН ИЗ ПЕРЕМЕННЫХ RAILWAY
# =======================
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
        "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
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
        # Читаем вывод в реальном времени
        while True:
            line = process.stderr.readline()
            if not line:
                break
                
            percent = parse_progress(line)
            if percent is not None:
                try:
                    await msg.edit_text(f"⏳ Скачивание... {percent:.1f}%")
                except:
                    pass
            logger.info(f"yt-dlp: {line.strip()}")

        process.wait()

        # Проверяем код возврата
        if process.returncode != 0:
            logger.error(f"yt-dlp вернул код ошибки: {process.returncode}")
            await msg.edit_text("❌ Ошибка при скачивании видео")
            return

        # Ищем файл с расширением .mp4
        all_files = os.listdir(tmpdir)
        logger.info(f"Файлы в tmpdir: {all_files}")
        
        for f in all_files:
            if f.endswith(".mp4"):
                downloaded_file = os.path.join(tmpdir, f)
                break

        # Если не нашли .mp4, ищем любой файл
        if not downloaded_file:
            for f in all_files:
                file_path = os.path.join(tmpdir, f)
                if os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
                    downloaded_file = file_path
                    break

        if not downloaded_file:
            logger.error(f"Файл не найден в {tmpdir}")
            await msg.edit_text("❌ Ошибка: файл не найден")
            return

        file_size = os.path.getsize(downloaded_file) / (1024 * 1024)
        logger.info(f"Найден файл: {downloaded_file}, размер: {file_size:.1f}MB")

        await msg.edit_text(f"📤 Отправляю видео... ({file_size:.1f}MB)")

        with open(downloaded_file, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption="✅ Готово!",
                write_timeout=60,
                read_timeout=60
            )

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        logger.info("Временная директория удалена")


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
    
    app.run_polling()


if __name__ == "__main__":
    main()
