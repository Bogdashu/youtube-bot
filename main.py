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
        "🎬 Отправь YouTube ссылку — я скачаю видео\n\n"
        "Поддерживаются:\n"
        "- Обычные видео\n"
        "- YouTube Shorts\n"
        "- Длинные видео (до 2GB)"
    )


# =========================
# DOWNLOAD
# =========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    msg = await update.message.reply_text("⏳ Получаю информацию о видео...")

    tmpdir = tempfile.mkdtemp(prefix="yt_")

    try:
        # =========================
        # 1. Получаем инфу о размере и названии
        # =========================
        info_cmd = [
            "yt-dlp",
            "--print", "%(filesize_approx)s",
            "--print", "%(title)s",
            "-f", "bv*[height<=1080]+ba/best",
            url,
        ]

        code, info_output = await run_command(info_cmd)

        filesize = 0
        title = "video"
        
        for line in info_output:
            if line.isdigit() and filesize == 0:
                filesize = int(line)
            elif line and not line.isdigit() and len(line) > 3:
                title = re.sub(r'[\\/*?:"<>|]', "", line)[:50]

        size_mb = format_mb(filesize) if filesize else 0

        # =========================
        # 2. Выбор качества
        # =========================
        if size_mb and size_mb <= MAX_BEST_MB:
            quality = "1080p"
            format_string = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best"
        else:
            quality = "720p"
            format_string = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best"

        await msg.edit_text(
            f"⏳ Скачиваю {quality}...\n"
            f"📊 Размер: ~{size_mb} MB\n"
            f"📹 {title[:30]}"
        )

        # =========================
        # 3. Скачивание с уникальным именем
        # =========================
        # Используем уникальное имя, чтобы избежать проблем с спецсимволами
        safe_title = re.sub(r'[^a-zA-Z0-9]', '_', title)[:30]
        outtmpl = os.path.join(tmpdir, f"{safe_title}.%(ext)s")

        cmd = [
            "yt-dlp",
            "-f", format_string,
            "-N", THREADS,
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
            "-o", outtmpl,
            url
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
                if current != last_percent and current % 10 == 0:
                    last_percent = current
                    try:
                        await msg.edit_text(
                            f"⏳ Скачиваю {quality}... ({size_mb} MB)\n"
                            f"📥 {current}%\n"
                            f"⏱️ Для длинных видео可能需要 больше времени"
                        )
                    except:
                        pass

        await process.wait()

        # =========================
        # 4. Улучшенный поиск видео файла
        # =========================
        await asyncio.sleep(2)  # Даем время на завершение записи
        
        downloaded_file = None
        all_files = []
        
        # Рекурсивно ищем все файлы
        for root, dirs, files in os.walk(tmpdir):
            for file in files:
                file_path = os.path.join(root, file)
                file_size = os.path.getsize(file_path)
                all_files.append((file_path, file_size, file))
        
        # Сортируем по размеру (от большего к меньшему)
        all_files.sort(key=lambda x: x[1], reverse=True)
        
        # Ищем MP4 файл
        for file_path, file_size, file_name in all_files:
            if file_name.lower().endswith('.mp4') and file_size > 100000:
                downloaded_file = file_path
                break
        
        # Если MP4 не найден, берем любой большой файл
        if not downloaded_file:
            for file_path, file_size, file_name in all_files:
                if file_size > 1000000:  # больше 1MB
                    downloaded_file = file_path
                    break

        if not downloaded_file:
            debug_msg = "❌ Видео не найдено\n\nНайденные файлы:\n"
            for file_path, file_size, file_name in all_files[:3]:
                debug_msg += f"- {file_name} ({format_mb(file_size)} MB)\n"
            await msg.edit_text(debug_msg)
            return

        final_size = format_mb(os.path.getsize(downloaded_file))

        # =========================
        # 5. Проверка размера для Telegram
        # =========================
        if final_size > 1900:
            await msg.edit_text(
                f"❌ Видео слишком большое для Telegram\n"
                f"📦 Размер: {final_size} MB (макс. 1900 MB)\n"
                f"🎬 Качество: {quality}"
            )
            return

        # =========================
        # 6. Отправка видео
        # =========================
        await msg.edit_text(
            f"📤 Отправляю видео...\n"
            f"🎬 {quality}\n"
            f"📦 {final_size} MB"
        )

        with open(downloaded_file, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=f"✅ Готово!\n"
                       f"🎬 {quality}\n"
                       f"📦 {final_size} MB\n"
                       f"📹 {title[:30]}",
                supports_streaming=True,
                read_timeout=600,  # Увеличил таймауты для длинных видео
                write_timeout=600,
                connect_timeout=600,
                pool_timeout=600,
            )

        await msg.delete()

    except asyncio.TimeoutError:
        await msg.edit_text("❌ Превышено время ожидания. Видео слишком длинное?")
    except Exception as e:
        error_text = str(e)
        print(f"Ошибка: {error_text}")
        await msg.edit_text(f"❌ Ошибка: {error_text[:200]}")
        
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except:
            pass


# =========================
# MAIN
# =========================
def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Вставь токен!\n")
        print("Вариант 1 - переменная окружения:")
        print("  export BOT_TOKEN='твой_токен'")
        print("  python bot.py\n")
        print("Вариант 2 - прямо в коде:")
        print("  TOKEN = 'твой_токен'")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    print("🤖 Bot started...")
    print(f"📝 Потоков: {THREADS}")
    print(f"🎬 Максимальный размер для 1080p: {MAX_BEST_MB} MB")
    
    # Проверяем наличие yt-dlp
    import shutil as sh
    if sh.which("yt-dlp"):
        print("✅ yt-dlp найден")
    else:
        print("❌ yt-dlp не найден! Установите: pip install yt-dlp")
    
    if sh.which("ffmpeg"):
        print("✅ ffmpeg найден")
    else:
        print("⚠️ ffmpeg не найден (нужен для звука)")
        print("   Ubuntu: sudo apt install ffmpeg")
        print("   Mac: brew install ffmpeg")
    
    app.run_polling()


if __name__ == "__main__":
    main()
