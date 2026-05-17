import os
import tempfile
import shutil
import yt_dlp
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

# Прогресс бар для Telegram
class TelegramProgressHook:
    def __init__(self, msg, update):
        self.msg = msg
        self.update = update
        self.last_percent = 0
    
    def progress_hook(self, d):
        if d['status'] == 'downloading':
            if 'total_bytes' in d:
                percent = d['downloaded_bytes'] / d['total_bytes'] * 100
            elif 'total_bytes_estimate' in d:
                percent = d['downloaded_bytes'] / d['total_bytes_estimate'] * 100
            else:
                return
            
            # Обновляем каждые 5%
            if percent - self.last_percent >= 5:
                self.last_percent = percent
                try:
                    self.msg.edit_text(f"⏳ Скачиваю видео...\n{percent:.1f}%")
                except:
                    pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку — я скачаю видео\n"
        "Поддерживаются длинные видео!"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    msg = await update.message.reply_text("⏳ Подготовка к скачиванию...")

    # Создаем временную папку
    tmpdir = tempfile.mkdtemp(prefix="yt_")
    
    # Настройки для оптимальной загрузки длинных видео
    ydl_opts = {
        'format': 'best[height<=720][ext=mp4]/best[ext=mp4]/best',
        'outtmpl': os.path.join(tmpdir, 'video.%(ext)s'),
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'concurrent_fragment_downloads': 8,  # Параллельная загрузка фрагментов
        'retries': 10,  # Повторные попытки
        'fragment_retries': 10,  # Повтор фрагментов
        'socket_timeout': 300,  # Таймаут 5 минут
    }
    
    # Добавляем хук для прогресса
    progress_hook = TelegramProgressHook(msg, update)
    ydl_opts['progress_hooks'] = [progress_hook.progress_hook]
    
    downloaded_file = None
    
    try:
        # Скачиваем видео
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await msg.edit_text("⏳ Скачиваю видео... 0%")
            
            # Запускаем скачивание в отдельном потоке
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, ydl.download, [url])
        
        # Ищем скачанный файл
        for f in os.listdir(tmpdir):
            path = os.path.join(tmpdir, f)
            if os.path.isfile(path) and any(f.endswith(ext) for ext in ['.mp4', '.mkv', '.webm']):
                downloaded_file = path
                break
        
        if not downloaded_file:
            await msg.edit_text("❌ Ошибка: файл не найден")
            return
        
        # Проверяем размер
        file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        
        if file_size_mb > 50:
            await msg.edit_text(
                f"⚠️ Видео весит {file_size_mb:.1f} MB\n"
                f"Telegram боты могут отправлять только до 50 MB\n\n"
                f"💡 Попробуй скачать в меньшем качестве:\n"
                f"Отправь ссылку с командой: /480p {url}\n"
                f"Или /360p {url}"
            )
            return
        
        # Отправляем видео
        await msg.edit_text(f"📤 Отправляю видео... ({file_size_mb:.1f} MB)")
        
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action="upload_video"
        )
        
        with open(downloaded_file, "rb") as video:
            await update.message.reply_video(
                video=video,
                caption=f"✅ Готово!\n📊 {file_size_mb:.1f} MB\n🎬 720p",
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600,
            )
        
        await msg.delete()
        
    except Exception as e:
        error_msg = str(e)
        print(f"Error: {error_msg}")  # Для логов
        await msg.edit_text(f"❌ Ошибка: {error_msg[:200]}")
    
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

# Команды для разных качеств
async def download_720p(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await download_with_quality(update, context, 720)

async def download_480p(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await download_with_quality(update, context, 480)

async def download_360p(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await download_with_quality(update, context, 360)

async def download_with_quality(update: Update, context: ContextTypes.DEFAULT_TYPE, quality: int):
    if not context.args:
        await update.message.reply_text(f"❌ Использование: /{quality}p <YouTube URL>")
        return
    
    url = context.args[0]
    
    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Неверная YouTube ссылка")
        return
    
    msg = await update.message.reply_text(f"⏳ Скачиваю видео в {quality}p...")
    
    tmpdir = tempfile.mkdtemp(prefix="yt_")
    
    ydl_opts = {
        'format': f'best[height<={quality}][ext=mp4]/best[height<={quality}]/best',
        'outtmpl': os.path.join(tmpdir, 'video.%(ext)s'),
        'noplaylist': True,
        'quiet': True,
        'concurrent_fragment_downloads': 8,
        'retries': 10,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, ydl.download, [url])
        
        # Ищем файл и отправляем
        for f in os.listdir(tmpdir):
            path = os.path.join(tmpdir, f)
            if os.path.isfile(path):
                file_size_mb = os.path.getsize(path) / (1024 * 1024)
                await msg.edit_text(f"📤 Отправляю {quality}p видео...")
                
                with open(path, "rb") as video:
                    await update.message.reply_video(
                        video=video,
                        caption=f"✅ Видео {quality}p\n📊 {file_size_mb:.1f} MB",
                        supports_streaming=True,
                        read_timeout=600,
                        write_timeout=600,
                    )
                
                await msg.delete()
                break
                
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:200]}")
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

    print("🤖 Бот запущен с yt-dlp библиотекой!")
    print("✅ Оптимизировано для длинных видео")
    app.run_polling()

if __name__ == "__main__":
    main()
