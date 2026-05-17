import os
import re
import tempfile
import subprocess
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

TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")

def parse_progress(line):
    match = progress_regex.search(line)
    if match:
        return float(match.group(1))
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку — я скачаю видео\n"
        "Оптимизировано для длинных видео (до 2 часов)"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    msg = await update.message.reply_text("⏳ Скачиваю видео...")

    tmpdir = tempfile.mkdtemp(prefix="yt_")
    outtmpl = os.path.join(tmpdir, "video.%(ext)s")

    # =========================
    # ОПТИМИЗАЦИИ ДЛЯ ДЛИННЫХ ВИДЕО
    # =========================
    format_string = "best[height<=720][ext=mp4]/best[ext=mp4]/best"
    
    cmd = [
        "yt-dlp",  # Используем напрямую, быстрее
        "-f", format_string,
        "-N", "16",  # Больше соединений для скорости
        "--newline",
        "-o", outtmpl,
        "--no-mux",  # Не использовать внешний muxer
        "--force-overwrites",
        "--no-part",
        "--fragment-retries", "20",  # Больше попыток
        "--retries", "20",
        "--socket-timeout", "600",  # 10 минут таймаут
        "--buffer-size", "16K",  # Увеличенный буфер
        "--http-chunk-size", "10M",  # Чанки по 10MB
        "--no-check-certificates",
        "--prefer-free-formats",
        url
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    downloaded_file = None
    last_percent = 0
    stall_count = 0

    try:
        # Устанавливаем таймаут для всего процесса
        process_start_time = asyncio.get_event_loop().time()
        
        for line in process.stdout:
            percent = parse_progress(line)

            if percent is not None:
                # Проверяем прогресс (если застряло - возможно проблема)
                if percent == last_percent:
                    stall_count += 1
                else:
                    stall_count = 0
                    last_percent = percent
                
                # Если застряло на 30+ секунд, пробуем обновить
                if stall_count > 60:  # ~30 секунд без прогресса
                    await msg.edit_text(
                        f"⏳ Видео загружается медленно...\n"
                        f"Прогресс: {percent:.1f}%\n"
                        f"Пожалуйста, подождите"
                    )
                    stall_count = 0
                else:
                    try:
                        await msg.edit_text(
                            f"⏳ Скачиваю видео...\n{percent:.1f}%\n"
                            f"(Длинное видео, требуется время)"
                        )
                    except:
                        pass

        # Ждем завершения с таймаутом
        try:
            process.wait(timeout=1800)  # 30 минут максимум
        except subprocess.TimeoutExpired:
            process.kill()
            await msg.edit_text("❌ Видео слишком длинное, превышен таймаут (30 минут)")
            return

        # Ищем видео
        for f in os.listdir(tmpdir):
            path = os.path.join(tmpdir, f)
            if os.path.isfile(path) and (f.endswith(".mp4") or f.endswith(".mkv") or f.endswith(".webm")):
                downloaded_file = path
                break

        if not downloaded_file:
            await msg.edit_text("❌ Ошибка: файл не найден")
            return

        file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        
        # Если файл больше 50 MB
        if file_size_mb > 50:
            await msg.edit_text(
                f"⚠️ Видео весит {file_size_mb:.1f} MB\n"
                f"Telegram принимает только до 50 MB\n\n"
                f"Попробуйте:\n"
                f"• /480p - для меньшего качества\n"
                f"• /360p - для маленького размера\n\n"
                f"Или скачайте видео сами: yt-dlp '{url}'"
            )
            return

        # Отправляем с прогрессом
        await msg.edit_text(f"📤 Отправляю видео... ({file_size_mb:.1f} MB)")

        with open(downloaded_file, "rb") as video:
            await update.message.reply_video(
                video=video,
                caption=f"✅ Готово!\n📊 {file_size_mb:.1f} MB\n🎬 720p",
                supports_streaming=True,
                read_timeout=900,  # 15 минут
                write_timeout=900,
                connect_timeout=900,
                pool_timeout=900,
            )

        await msg.delete()

    except subprocess.TimeoutExpired:
        await msg.edit_text("❌ Превышено время скачивания (видео слишком длинное)")
    except Exception as e:
        error_msg = str(e)
        print(f"Error: {error_msg}")
        
        if "413" in error_msg:
            await msg.edit_text("❌ Файл слишком большой для Telegram (максимум 50 MB)")
        elif "timed out" in error_msg.lower():
            await msg.edit_text("❌ Таймаут: видео слишком длинное или медленный интернет")
        else:
            await msg.edit_text(f"❌ Ошибка: {error_msg[:150]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# =========================
# ДОПОЛНИТЕЛЬНЫЕ КОМАНДЫ ДЛЯ РАЗНОГО КАЧЕСТВА
# =========================
async def download_720p(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await download_with_quality(update, context, "720")

async def download_480p(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await download_with_quality(update, context, "480")

async def download_360p(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await download_with_quality(update, context, "360")

async def download_with_quality(update: Update, context: ContextTypes.DEFAULT_TYPE, quality: str):
    if not context.args:
        await update.message.reply_text(f"❌ Использование: /{quality}p <YouTube URL>")
        return
    
    url = context.args[0]
    
    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Неверная YouTube ссылка")
        return
    
    msg = await update.message.reply_text(f"⏳ Скачиваю {quality}p...")
    
    tmpdir = tempfile.mkdtemp(prefix="yt_")
    outtmpl = os.path.join(tmpdir, "video.%(ext)s")
    
    format_string = f"best[height<={quality}][ext=mp4]/best[ext=mp4]/best"
    
    cmd = ["yt-dlp", "-f", format_string, "-N", "16", "--newline", "-o", outtmpl, url]
    
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    
    try:
        for line in process.stdout:
            percent = parse_progress(line)
            if percent is not None:
                try:
                    await msg.edit_text(f"⏳ Скачиваю {quality}p...\n{percent:.1f}%")
                except:
                    pass
        
        process.wait()
        
        for f in os.listdir(tmpdir):
            path = os.path.join(tmpdir, f)
            if os.path.isfile(path):
                file_size_mb = os.path.getsize(path) / (1024 * 1024)
                
                if file_size_mb > 50:
                    await msg.edit_text(f"❌ Видео {quality}p весит {file_size_mb:.1f} MB > 50 MB\nПопробуйте качество ниже")
                    return
                
                await msg.edit_text(f"📤 Отправляю {quality}p...")
                
                with open(path, "rb") as video:
                    await update.message.reply_video(
                        video=video,
                        caption=f"✅ {quality}p\n📊 {file_size_mb:.1f} MB",
                        read_timeout=600,
                        write_timeout=600,
                    )
                
                await msg.delete()
                break
                
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:150]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Вставь токен!")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("720p", download_720p))
    app.add_handler(CommandHandler("480p", download_480p))
    app.add_handler(CommandHandler("360p", download_360p))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot started...")
    print("✅ Оптимизировано для длинных видео (до 2 часов)")
    print("💡 Используйте /480p или /360p для больших видео")
    app.run_polling()

if __name__ == "__main__":
    main()
