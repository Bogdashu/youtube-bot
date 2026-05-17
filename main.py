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
    # Пробуем разные способы вызова yt-dlp
    commands_to_try = [
        ["yt-dlp", "-j", "--skip-download", url],
        ["yt-dlp", "-J", "--skip-download", url],
        ["python", "-m", "yt_dlp", "-j", "--skip-download", url],
        ["python3", "-m", "yt_dlp", "-j", "--skip-download", url],
    ]
    
    for cmd in commands_to_try:
        try:
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True,
                timeout=30
            )
            if result.returncode == 0 and result.stdout:
                data = json.loads(result.stdout)
                return data
        except:
            continue
    
    return None


def check_ytdlp_installed():
    """Проверяем, установлен ли yt-dlp"""
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
        return True
    except:
        try:
            subprocess.run(["python", "-m", "yt_dlp", "--version"], capture_output=True, check=True)
            return True
        except:
            return False


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
        # Проверяем установку yt-dlp
        if not check_ytdlp_installed():
            await msg.edit_text(
                "❌ yt-dlp не установлен!\n"
                "Установите его командой:\n"
                "`pip install yt-dlp`",
                parse_mode='Markdown'
            )
            return
        
        # Получаем информацию о видео
        video_info = await get_video_info(url)
        
        if not video_info:
            # Если не удалось получить инфу, просто скачиваем 720p (безопасный вариант)
            await msg.edit_text(
                "⚠️ Не удалось оценить размер видео.\n"
                "Скачиваю в 720p (безопасный вариант)..."
            )
            quality = "720p"
            format_string = (
                "best[height<=720][ext=mp4]/"
                "best[ext=mp4]/"
                "best"
            )
        else:
            # Получаем длительность видео
            duration = video_info.get('duration', 0)
            
            # Оцениваем размер для 1080p (2.5 Мбит/с для 1080p)
            estimated_size_1080p = (2.5 * duration) / 8
            
            # Выбираем качество
            if estimated_size_1080p < 100 and duration > 0:
                quality = "1080p"
                format_string = (
                    "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
                    "best[height<=1080][ext=mp4]/"
                    "best[height<=1080]/"
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
            "yt-dlp",  # Используем прямую команду
            "-f", format_string,
            "-N", "8",
            "--newline",
            "-o", outtmpl,
            "--no-playlist",
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

        if process.returncode != 0:
            await msg.edit_text("❌ Ошибка при скачивании видео")
            return

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
        
        if file_size_mb > 50:
            await msg.edit_text(
                f"⚠️ Видео весит {file_size_mb:.1f} МБ.\n"
                f"Telegram боты могут отправлять видео до 50 МБ.\n"
                f"Попробуйте скачать в более низком качестве."
            )
            return

        # Отправка
        await msg.edit_text(f"📤 Отправляю видео... ({quality})")

        with open(downloaded_file, "rb") as video:
            await update.message.reply_video(
                video=video,
                caption=f"✅ Готово!\n📺 Качество: {quality}\n📊 Размер: {file_size_mb:.1f} МБ",
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600,
            )

        await msg.delete()

    except subprocess.TimeoutExpired:
        await msg.edit_text("❌ Превышено время ожидания")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")
        print(f"Error: {e}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------- MAIN ----------------
def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Вставь токен в переменную TOKEN!")
        return

    # Проверяем наличие yt-dlp при запуске
    if not check_ytdlp_installed():
        print("❌ yt-dlp не установлен!")
        print("Установите его командой: pip install yt-dlp")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
