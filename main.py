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
        "🎬 Отправь YouTube ссылку — я скачаю видео\n\n"
        "Поддерживаются:\n"
        "- YouTube видео\n"
        "- Shorts\n"
        "- Плейлисты (первое видео)"
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
        # Получаем информацию о видео
        # =========================
        info_cmd = [
            "python", "-m", "yt_dlp",
            "--print", "%(filesize_approx)s",
            "--print", "%(title)s",
            "--print", "%(duration)s",
            "-f", "bestvideo+bestaudio/best",
            url
        ]
        
        code, info_output = await run_command(info_cmd)
        
        filesize = 0
        title = "video"
        duration = 0
        
        lines = [line for line in info_output if line.strip()]
        for line in lines:
            if line.isdigit() and len(line) > 3:
                if filesize == 0:
                    filesize = int(line)
            elif line and not line.isdigit() and ":" not in line and len(line) > 3 and len(line) < 100:
                title = re.sub(r'[\\/*?:"<>|]', "", line)[:50]
            elif ":" in line and len(line) < 20:
                try:
                    parts = line.split(":")
                    if len(parts) == 2:
                        duration = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                except:
                    pass

        size_mb = format_mb(filesize) if filesize else 0
        
        # Если размер неизвестен, оцениваем по длительности
        if size_mb == 0 and duration > 0:
            # Примерная оценка: 10MB в минуту для 1080p
            size_mb = round(duration / 60 * 10, 1)

        # =========================
        # Выбор качества
        # =========================
        if size_mb <= 100:
            quality = "1080p"
            format_string = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]"
        elif size_mb <= 180:
            quality = "720p"
            format_string = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]"
        else:
            quality = "480p"
            format_string = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]"

        await msg.edit_text(
            f"⏳ Скачиваю {quality}...\n"
            f"📊 Размер: ~{size_mb} MB\n"
            f"📹 Название: {title}"
        )

        # =========================
        # Скачивание видео
        # =========================
        output_template = os.path.join(tmpdir, f"{title}.%(ext)s")

        cmd = [
            "python", "-m", "yt_dlp",
            "-f", format_string,
            "-N", THREADS,
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
            "-o", output_template,
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
                            f"⏳ Скачиваю {quality}...\n"
                            f"📊 {current}%"
                        )
                    except:
                        pass

        await process.wait()

        # =========================
        # Поиск скачанного файла
        # =========================
        await asyncio.sleep(1)  # Ждем завершения записи
        
        downloaded_file = None
        all_files = []
        
        # Рекурсивно ищем все файлы
        for root, dirs, files in os.walk(tmpdir):
            for file in files:
                file_path = os.path.join(root, file)
                all_files.append(file_path)
                
                # Проверяем видео расширения
                if file.lower().endswith(('.mp4', '.mkv', '.webm', '.mov', '.avi')):
                    file_size = os.path.getsize(file_path)
                    if file_size > 100000:  # больше 100KB
                        downloaded_file = file_path
                        break
            if downloaded_file:
                break
        
        # Если не нашли по расширениям, берем самый большой файл
        if not downloaded_file and all_files:
            all_files.sort(key=lambda x: os.path.getsize(x) if os.path.isfile(x) else 0, reverse=True)
            for f in all_files:
                if os.path.isfile(f) and os.path.getsize(f) > 100000:
                    downloaded_file = f
                    break

        if not downloaded_file:
            # Логируем для отладки
            debug_files = [os.path.basename(f) for f in all_files]
            await msg.edit_text(
                f"❌ Видео не найдено\n"
                f"Файлы в папке: {debug_files[:5]}"
            )
            return

        final_size = format_mb(os.path.getsize(downloaded_file))

        # =========================
        # Проверка размера для Telegram
        # =========================
        if final_size > 1900:
            await msg.edit_text(
                f"❌ Видео слишком большое для Telegram\n"
                f"📦 Размер: {final_size} MB (макс. 1900 MB)"
            )
            return

        # =========================
        # Отправка видео
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
                       f"🎬 Качество: {quality}\n"
                       f"📦 Размер: {final_size} MB\n"
                       f"📹 {title}",
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600,
            )

        await msg.delete()

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
        print("❌ Ошибка: Вставь токен бота в переменную TOKEN")
        print("Способы:\n")
        print("1. Через переменную окружения:")
        print("   export BOT_TOKEN='твой_токен'")
        print("   python bot.py\n")
        print("2. Прямо в коде:")
        print("   TOKEN = 'твой_токен'")
        return

    # Создаем приложение
    app = Application.builder().token(TOKEN).build()

    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Бот запущен...")
    print(f"📝 Используется потоков: {THREADS}")
    print("✅ Готов к работе!")
    
    # Запускаем бота
    app.run_polling()


if __name__ == "__main__":
    main()
