import os
import re
import json
import tempfile
import subprocess
import shutil
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

# Регулярка для процентов (из вывода yt-dlp)
progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")

def parse_progress(line: str):
    match = progress_regex.search(line)
    return float(match.group(1)) if match else None

def get_video_info(url: str):
    """Возвращает (размер_в_мб, название_видео) без скачивания."""
    cmd = ["python", "-m", "yt_dlp", "-J", "--no-playlist", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(result.stdout)

    # Ищем максимальный размер среди всех форматов
    max_size = 0
    for f in info.get("formats", []):
        if f.get("filesize"):
            max_size = max(max_size, f["filesize"])
    size_mb = max_size / (1024 * 1024)
    title = info.get("title", "video")
    return size_mb, title

def split_file(file_path: str, part_size_mb: int = 45):
    """
    Генератор, который читает файл и выдаёт (часть_номер, временный_файл_путь).
    Каждая часть удаляется после отправки (вызывающий код должен удалить).
    """
    part_size = part_size_mb * 1024 * 1024
    part_num = 1
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(part_size)
            if not chunk:
                break
            # Создаём временный файл для части
            fd, part_path = tempfile.mkstemp(suffix=f"_part_{part_num:03d}.mp4", prefix="yt_part_")
            os.close(fd)
            with open(part_path, "wb") as pf:
                pf.write(chunk)
            yield part_num, part_path
            part_num += 1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку\n"
        "✅ Видео до 45 МБ → приходит целиком, 1080p\n"
        "✅ Видео больше 45 МБ → разбивается на части (тоже 1080p)\n"
        "📦 Части склеиваются командой в терминале"
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
        await msg.edit_text(f"📊 Размер: {size_mb:.1f} МБ\n⏳ Скачиваю в максимальном качестве...")

        # 2. Всегда скачиваем в 1080p (лучшее видео+аудио)
        tmpdir = tempfile.mkdtemp(prefix="yt_")
        outtmpl = os.path.join(tmpdir, "video.%(ext)s")

        # Формат: лучшее видео + лучшее аудио → mp4
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

        # Отслеживаем прогресс скачивания
        for line in process.stdout:
            percent = parse_progress(line)
            if percent is not None:
                try:
                    await msg.edit_text(f"⏳ Скачиваю... {percent:.1f}% (итог ~{size_mb:.1f} МБ)")
                except:
                    pass

        process.wait()

        # Ищем скачанный mp4 файл
        for f in os.listdir(tmpdir):
            if f.endswith(".mp4"):
                downloaded_file = os.path.join(tmpdir, f)
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            await msg.edit_text("❌ Не удалось скачать видео")
            return

        final_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)

        # 3. Если файл ≤ 45 МБ – отправляем целиком
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
            # 4. Большой файл – разбиваем на части
            await msg.edit_text(f"📦 Видео {final_size_mb:.1f} МБ (>45). Разбиваю на части...")

            # Подсчитываем количество частей (приблизительно)
            parts_count = int(final_size_mb / 45) + 1
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

            # Разбиваем и отправляем части по одной (экономия диска)
            part_num = 1
            for part_num, part_path in split_file(downloaded_file, 45):
                part_size_mb = os.path.getsize(part_path) / (1024 * 1024)
                await msg.edit_text(f"📤 Отправляю часть {part_num} из {parts_count}...")
                with open(part_path, "rb") as pf:
                    await update.message.reply_document(
                        document=pf,
                        filename=f"video_1080p_part_{part_num:03d}_{parts_count}.mp4",
                        caption=f"Часть {part_num} из {parts_count} ({part_size_mb:.1f} МБ)"
                    )
                # Удаляем часть после отправки
                os.unlink(part_path)

            await msg.edit_text("✅ Все части отправлены!")
            await msg.delete()

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:200]}")
    finally:
        # Очистка временных файлов
        if 'tmpdir' in locals():
            shutil.rmtree(tmpdir, ignore_errors=True)

def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Вставь токен в переменную BOT_TOKEN")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Бот запущен (1080p с разбивкой больших видео)")
    app.run_polling()

if __name__ == "__main__":
    main()
