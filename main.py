import os
import re
import json
import tempfile
import subprocess
import shutil
import asyncio
from datetime import datetime, time as dt_time
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ========================
# ТОКЕН (на Railway задаётся переменная окружения BOT_TOKEN)
# ========================
TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

# Регулярка для процентов из вывода yt-dlp
progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")

def parse_progress(line: str):
    """Извлекает процент скачивания из строки вывода yt-dlp."""
    match = progress_regex.search(line)
    return float(match.group(1)) if match else None

def get_video_info(url: str):
    """
    Безопасно получает размер (МБ) и название видео через yt-dlp -J.
    Защита от NoneType и других ошибок.
    """
    try:
        cmd = ["python", "-m", "yt_dlp", "-J", "--no-playlist", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"yt-dlp metadata error (code {result.returncode}): {result.stderr}")
            return 0, "Неизвестное видео"

        info = json.loads(result.stdout)
        if not info:
            print("JSON metadata is empty")
            return 0, "Неизвестное видео"

        max_size = 0
        formats = info.get('formats')
        if formats and isinstance(formats, list):
            for f in formats:
                if f and isinstance(f, dict):
                    # Пробуем filesize, затем filesize_approx
                    f_size = f.get('filesize') or f.get('filesize_approx')
                    if f_size and isinstance(f_size, (int, float)):
                        max_size = max(max_size, f_size)

        # Если не нашли в форматах, пробуем прямой filesize из корня
        if max_size == 0:
            direct_size = info.get('filesize') or info.get('filesize_approx')
            if direct_size and isinstance(direct_size, (int, float)):
                max_size = direct_size

        size_mb = max_size / (1024 * 1024)
        title = info.get('title', 'Video')
        print(f"Video info: {title}, size: {size_mb:.2f} MB")
        return size_mb, title

    except json.JSONDecodeError as e:
        print(f"JSON decode error: {e}")
        return 0, "Неизвестное видео"
    except subprocess.TimeoutExpired:
        print("yt-dlp metadata timeout")
        return 0, "Неизвестное видео"
    except Exception as e:
        print(f"Unexpected error in get_video_info: {e}")
        return 0, "Неизвестное видео"

def split_file_generator(file_path: str, part_size_mb: int = 45):
    """
    Генератор, который читает файл и выдаёт (номер_части, путь_к_временному_файлу_части).
    Каждая часть после отправки должна быть удалена вызывающим кодом.
    """
    part_size = part_size_mb * 1024 * 1024
    part_num = 1
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(part_size)
            if not chunk:
                break
            fd, part_path = tempfile.mkstemp(suffix=f"_part_{part_num:03d}.mp4", prefix="yt_part_")
            os.close(fd)
            with open(part_path, "wb") as pf:
                pf.write(chunk)
            yield part_num, part_path
            part_num += 1

# ========================
# ОБРАБОТЧИКИ КОМАНД
# ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку\n"
        "✅ Видео до 45 МБ → приходит целиком, 1080p\n"
        "✅ Видео больше 45 МБ → разбивается на части (тоже 1080p)\n"
        "📦 Части склеиваются командой в терминале\n\n"
        "⏰ Бот работает ежедневно с 4:00 до 22:00 МСК."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtu" not in url and "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Поддерживаются только YouTube ссылки")
        return

    msg = await update.message.reply_text("📊 Анализирую видео...")

    try:
        # 1. Получаем размер и название
        size_mb, title = get_video_info(url)
        if size_mb <= 0:
            await msg.edit_text("❌ Не удалось определить размер видео. Попробуйте другую ссылку.")
            return

        await msg.edit_text(f"📊 Размер: {size_mb:.1f} МБ\n⏳ Скачиваю в максимальном качестве...")

        # 2. Всегда скачиваем в 1080p (лучшее видео + аудио)
        tmpdir = tempfile.mkdtemp(prefix="yt_")
        outtmpl = os.path.join(tmpdir, "video.%(ext)s")

        cmd = [
            "python", "-m", "yt_dlp",
            "--no-playlist",
            "-f", "bv*+ba/b",
            "--merge-output-format", "mp4",
            "--concurrent-fragments", "8",
            "--socket-timeout", "30",
            "--retries", "5",
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
                    await msg.edit_text(f"⏳ Скачиваю... {percent:.1f}% (оц. размер {size_mb:.1f} МБ)")
                except:
                    pass

        process.wait()

        # Ищем скачанный mp4
        for f in os.listdir(tmpdir):
            if f.endswith(".mp4"):
                downloaded_file = os.path.join(tmpdir, f)
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            await msg.edit_text("❌ Не удалось скачать видео")
            return

        final_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)

        # 3. Если файл ≤45 МБ — отправляем целиком
        if final_size_mb <= 45:
            await msg.edit_text(f"📤 Отправляю видео ({final_size_mb:.1f} МБ)...")
            with open(downloaded_file, "rb") as f:
                await update.message.reply_video(
                    video=f,
                    caption=f"✅ {title}\n📊 {final_size_mb:.1f} МБ\n🎬 1080p",
                    supports_streaming=True,
                    read_timeout=300,
                    write_timeout=300
                )
            await msg.delete()
        else:
            # 4. Большой файл — разбиваем на части
            parts_count = int(final_size_mb / 45) + 1
            await msg.edit_text(f"📦 Видео {final_size_mb:.1f} МБ (>45). Разбиваю на {parts_count} частей...")
            await update.message.reply_text(
                f"📥 **Видео разбито на {parts_count} частей**\n\n"
                f"**Как собрать (Windows):**\n"
                f"```\ncopy /b part_*.mp4 video.mp4\n```\n\n"
                f"**Как собрать (Linux/Mac):**\n"
                f"```\ncat part_*.mp4 > video.mp4\n```\n\n"
                f"📊 Общий размер: {final_size_mb:.1f} МБ\n"
                f"🎬 Качество: 1080p",
                parse_mode="Markdown"
            )

            for part_num, part_path in split_file_generator(downloaded_file, 45):
                part_size_mb = os.path.getsize(part_path) / (1024 * 1024)
                await msg.edit_text(f"📤 Отправляю часть {part_num} из {parts_count}...")
                with open(part_path, "rb") as pf:
                    await update.message.reply_document(
                        document=pf,
                        filename=f"video_1080p_part_{part_num:03d}_{parts_count}.mp4",
                        caption=f"Часть {part_num} из {parts_count} ({part_size_mb:.1f} МБ)"
                    )
                os.unlink(part_path)  # удаляем часть сразу после отправки

            await msg.edit_text("✅ Все части отправлены!")
            await msg.delete()

    except Exception as e:
        error_msg = str(e)
        print(f"Error in handle_message: {error_msg}")
        await msg.edit_text(f"❌ Ошибка: {error_msg[:200]}")
    finally:
        # Очищаем временную папку
        if 'tmpdir' in locals():
            shutil.rmtree(tmpdir, ignore_errors=True)

# ========================
# АВТОМАТИЧЕСКАЯ ОСТАНОВКА В 22:00 МСК
# ========================
async def shutdown_scheduler(app):
    """Фоновая задача, которая останавливает бота в 22:00 по Москве."""
    msk_tz = pytz.timezone('Europe/Moscow')
    while True:
        now_msk = datetime.now(msk_tz)
        target_time = dt_time(22, 0)      # 22:00
        # Если текущее время >= 22:00 и ещё не дошло до 22:05 (защита от многократного срабатывания)
        if now_msk.time() >= target_time and now_msk.time() < dt_time(22, 5):
            print("INFO: Bot is shutting down as per schedule (22:00 MSK).")
            await app.stop()
            break
        await asyncio.sleep(30)   # проверяем каждые 30 секунд

# ========================
# MAIN
# ========================
def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Вставь токен в переменную окружения BOT_TOKEN")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем фоновую задачу для остановки в 22:00
    loop = asyncio.get_event_loop()
    loop.create_task(shutdown_scheduler(app))

    print("🤖 Бот запущен (1080p с разбивкой больших видео)")
    print("⏰ Автоостановка в 22:00 МСК. Запуск по Cron в 4:00 МСК (настраивается в Railway).")
    app.run_polling()

if __name__ == "__main__":
    main()
