import os
import re
import tempfile
import subprocess
import shutil
import json

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =======================
# TOKEN
# =======================
TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def parse_progress(line):
    match = progress_regex.search(line)
    if match:
        return float(match.group(1))
    return None


async def get_video_info(url):
    """Получаем информацию о видео без скачивания"""
    cmd = [
        "python", "-m", "yt_dlp",
        "-j",  # вывод в JSON
        "--skip-download",
        url
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return data
    return None


def estimate_file_size(duration_seconds, bitrate_mbps=2.5):
    """Примерная оценка размера файла в МБ (для 1080p ~2.5 Мбит/с)"""
    # размер_МБ = (битрейт_Мбит/с * длительность_сек) / 8
    estimated_mb = (bitrate_mbps * duration_seconds) / 8
    return estimated_mb


# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку — я скачаю видео\n\n"
        "📊 Автовыбор качества:\n"
        "• 1080p — если размер < 100 МБ\n"
        "• 720p — если размер ≥ 100 МБ"
    )


# ---------------- DOWNLOAD ----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    msg = await update.message.reply_text("⏳ Получаю информацию о видео...")

    tmpdir = tempfile.mkdtemp(prefix="yt_")
    outtmpl = os.path.join(tmpdir, "video.%(ext)s")

    try:
        # Получаем информацию о видео
        video_info = await get_video_info(url)
        
        if not video_info:
            await msg.edit_text("❌ Не удалось получить информацию о видео")
            return
        
        duration = video_info.get('duration', 0)
        
        # Оцениваем размер для 1080p
        estimated_size_1080p = estimate_file_size(duration, bitrate_mbps=2.5)
        
        # Выбираем качество
        if estimated_size_1080p < 100:
            quality = "1080p"
            format_string = (
                "best[height<=1080][ext=mp4]/"
                "best[height<=1080]/"
                "best[ext=mp4]/"
                "best"
            )
        else:
            quality = "720p"
            format_string = (
                "best[height<=720][ext=mp4]/"
                "best[ext=mp4]/"
                "best"
            )
        
        await msg.edit_text(
            f"⏳ Скачиваю видео...\n"
            f"📺 Качество: {quality}\n"
            f"📊 Примерный размер: {estimated_size_1080p:.1f} МБ"
        )
        
        # Команда для скачивания
        cmd = [
            "python", "-m", "yt_dlp",
            "-f", format_string,
            "-N", "8",
            "--newline",
            "-o", outtmpl,
            url
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        downloaded_file = None

        for line in process.stdout:
            percent = parse_progress(line)
            if percent is not None:
                try:
                    await msg.edit_text(
                        f"⏳ Скачиваю видео...\n"
                        f"📺 {quality}\n"
                        f"{percent:.1f}%"
                    )
                except:
                    pass

        process.wait()

        # Ищем видео
        for f in os.listdir(tmpdir):
            path = os.path.join(tmpdir, f)
            if os.path.isfile(path) and (
                f.endswith(".mp4") or f.endswith(".mkv") or f.endswith(".webm")
            ):
                downloaded_file = path
                break

        if not downloaded_file:
            await msg.edit_text("❌ Ошибка: файл не найден")
            return

        # Проверяем реальный размер файла
        file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        
        # Если реальный размер превышает 100 МБ, пробуем скачать в 720p
        if file_size_mb > 100 and quality == "1080p":
            await msg.edit_text(
                f"⚠️ Реальный размер ({file_size_mb:.1f} МБ) превышает 100 МБ.\n"
                f"⏳ Пробую скачать в 720p..."
            )
            
            # Скачиваем в 720p
            format_string_720 = (
                "best[height<=720][ext=mp4]/"
                "best[ext=mp4]/"
                "best"
            )
            
            cmd_720 = [
                "python", "-m", "yt_dlp",
                "-f", format_string_720,
                "-N", "8",
                "-o", outtmpl.replace(".%(ext)s", "_720.%(ext)s"),
                url
            ]
            
            process_720 = subprocess.Popen(
                cmd_720,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            for line in process_720.stdout:
                percent = parse_progress(line)
                if percent is not None:
                    try:
                        await msg.edit_text(
                            f"⏳ Скачиваю 720p...\n{percent:.1f}%"
                        )
                    except:
                        pass
            
            process_720.wait()
            
            # Ищем новый файл
            for f in os.listdir(tmpdir):
                if "_720" in f and (f.endswith(".mp4") or f.endswith(".mkv") or f.endswith(".webm")):
                    downloaded_file = os.path.join(tmpdir, f)
                    quality = "720p"
                    break

        # Отправка
        await msg.edit_text(f"📤 Отправляю видео... ({quality})")

        with open(downloaded_file, "rb") as video:
            await update.message.reply_video(
                video=video,
                caption=f"✅ Готово!\n📺 Качество: {quality}",
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600,
            )

        await msg.delete()

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------- MAIN ----------------
def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Вставь токен!")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
