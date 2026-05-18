import os
import re
import tempfile
import subprocess
import shutil
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# --- Получаем переменные окружения, установленные на Railway ---
TOKEN = os.getenv("BOT_TOKEN")
# URL локального Bot API (будет доступен по имени сервиса в Docker Compose)
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL", "http://telegram-bot-api:8081")

# Регулярное выражение для отслеживания прогресса скачивания
progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")

def parse_progress(line: str):
    """Извлекает процент скачивания из логов yt-dlp"""
    match = progress_regex.search(line)
    return float(match.group(1)) if match else None

# --- Функция запуска бота ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку!\n"
        "✅ **Максимальное качество (1080p)**\n"
        "✅ **Видео до 2 ГБ** (без разбивки на части)\n"
        "✅ **Абсолютно безопасно для вашего аккаунта**\n\n"
        "🚀 Бот готов к работе!",
        parse_mode="Markdown"
    )

# --- Главная функция обработки сообщений ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    # Проверка на ссылку YouTube
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Пожалуйста, отправьте ссылку на YouTube.")
        return

    # Сообщение о начале работы
    msg = await update.message.reply_text("⏳ Начинаю скачивание...")

    # --- Настройка yt-dlp для максимального качества 1080p ---
    # Создаем временную папку для видео
    with tempfile.TemporaryDirectory() as tmpdir:
        outtmpl = os.path.join(tmpdir, "video.%(ext)s")

        # Ключевая команда для скачивания:
        # -f "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
        #   Эта команда выбирает лучшее видео с разрешением до 1080p, лучшее аудио и объединяет их.
        #   Если такого нет, выбирает лучший доступный файл.
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "--merge-output-format", "mp4",
            "-N", "8",
            "--newline",
            "-o", outtmpl,
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            url
        ]

        # Запускаем процесс скачивания
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

        video_file = None
        # Читаем вывод процесса и обновляем прогресс
        for line in process.stdout:
            percent = parse_progress(line)
            if percent is not None:
                try:
                    await msg.edit_text(f"⏳ Скачивание... {percent:.1f}%")
                except:
                    pass

        process.wait()

        # Ищем скачанный файл
        for f in os.listdir(tmpdir):
            if f.endswith(('.mp4', '.mkv', '.webm')):
                video_file = os.path.join(tmpdir, f)
                break

        if not video_file:
            await msg.edit_text("❌ Не удалось скачать видео. Возможно, видео недоступно.")
            return

        size_mb = os.path.getsize(video_file) / (1024 * 1024)
        await msg.edit_text(f"📤 Отправляю видео ({size_mb:.1f} MB)...")

        # Отправляем видео в чат. Локальный Bot API позволяет обойти лимит в 50 МБ.
        with open(video_file, "rb") as v:
            await update.message.reply_video(
                video=v,
                caption=f"✅ Видео готово!\n📊 Размер: {size_mb:.1f} MB\n🎬 Качество: 1080p",
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600
            )
        await msg.delete()

def main():
    if not TOKEN:
        print("❌ Ошибка: BOT_TOKEN не установлен!")
        return

    # Создаем приложение и указываем URL нашего локального Bot API сервера
    # Это ключевой момент для обхода лимита в 50 МБ.
    app = Application.builder().token(TOKEN).base_url(LOCAL_BOT_API_URL).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Бот успешно запущен на локальном Bot API сервере (лимит файлов до 2 ГБ)!")
    app.run_polling()

if __name__ == "__main__":
    main()
