import os
import re
import tempfile
import subprocess
import shutil
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN")

# Регулярка для прогресса скачивания
progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")

def parse_progress(line):
    m = progress_regex.search(line)
    return float(m.group(1)) if m else None

def split_file(file_path, part_size_mb=45):
    """Разбивает файл на части и возвращает список путей к временным файлам"""
    parts_dir = tempfile.mkdtemp(prefix="parts_")
    part_size = part_size_mb * 1024 * 1024
    part_num = 1
    part_paths = []

    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(part_size)
            if not chunk:
                break
            part_path = os.path.join(parts_dir, f"part_{part_num:03d}.mp4")
            with open(part_path, 'wb') as pf:
                pf.write(chunk)
            part_paths.append(part_path)
            part_num += 1

    return parts_dir, part_paths

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку\n"
        "✅ Большие видео разбиваются на части\n"
        "✅ Нет таймаутов — отправляю по частям сразу"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Не YouTube ссылка")
        return

    msg = await update.message.reply_text("⏳ Скачиваю видео...")

    # Временная папка для скачивания
    tmpdir = tempfile.mkdtemp(prefix="yt_")
    outtmpl = os.path.join(tmpdir, "video.%(ext)s")

    # Команда для скачивания
    cmd = [
        "yt-dlp",
        "-f", "best[height<=720][ext=mp4]/best",
        "-N", "8",
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

    video_file = None

    try:
        # Отслеживаем прогресс с таймаутом
        last_update = asyncio.get_event_loop().time()
        
        for line in process.stdout:
            percent = parse_progress(line)
            if percent is not None:
                now = asyncio.get_event_loop().time()
                # Обновляем сообщение не чаще 1 раза в 5 секунд
                if now - last_update >= 5:
                    last_update = now
                    try:
                        await msg.edit_text(f"⏳ Скачиваю... {percent:.1f}%")
                    except:
                        pass

        # Ждём завершения процесса
        try:
            process.wait(timeout=300)  # 5 минут максимум
        except subprocess.TimeoutExpired:
            process.kill()
            await msg.edit_text("❌ Скачивание заняло слишком много времени")
            return

        # Ищем скачанный файл
        for f in os.listdir(tmpdir):
            path = os.path.join(tmpdir, f)
            if os.path.isfile(path) and f.endswith(('.mp4', '.mkv')):
                video_file = path
                break

        if not video_file:
            await msg.edit_text("❌ Файл не найден")
            return

        size_mb = os.path.getsize(video_file) / (1024 * 1024)

        # Если файл ≤45 МБ — отправляем целиком
        if size_mb <= 45:
            await msg.edit_text(f"📤 Отправляю ({size_mb:.1f} MB)...")
            with open(video_file, "rb") as v:
                await update.message.reply_video(
                    video=v,
                    caption=f"✅ {size_mb:.1f} MB",
                    supports_streaming=True
                )
            await msg.delete()

        # Если файл >45 МБ — разбиваем на части
        else:
            await msg.edit_text(f"📦 Видео {size_mb:.1f} MB, разбиваю на части...")
            parts_dir, parts = split_file(video_file, 45)

            # Отправляем инструкцию один раз
            await update.message.reply_text(
                f"📥 **Видео разбито на {len(parts)} частей**\n\n"
                f"**Как собрать (Windows):**\n`copy /b part_*.mp4 video.mp4`\n\n"
                f"**Как собрать (Linux/Mac):**\n`cat part_*.mp4 > video.mp4`\n\n"
                f"📊 Общий размер: {size_mb:.1f} MB",
                parse_mode="Markdown"
            )

            # Отправляем каждую часть с обновлением прогресса
            for i, part in enumerate(parts, 1):
                part_size = os.path.getsize(part) / (1024 * 1024)
                await msg.edit_text(f"📤 Отправляю часть {i} из {len(parts)} ({part_size:.1f} MB)...")

                with open(part, "rb") as p:
                    await update.message.reply_document(
                        document=p,
                        filename=f"part_{i:03d}_{len(parts)}.mp4"
                    )
                os.unlink(part)  # Удаляем после отправки

            # Удаляем папку с частями
            shutil.rmtree(parts_dir, ignore_errors=True)
            await msg.edit_text("✅ Все части отправлены!")
            await msg.delete()

    except subprocess.TimeoutExpired:
        await msg.edit_text("❌ Таймаут: видео слишком большое или медленное соединение")
    except Exception as e:
        error_msg = str(e)
        print(f"Error: {error_msg}")
        await msg.edit_text(f"❌ {error_msg[:100]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def main():
    if not TOKEN:
        print("❌ BOT_TOKEN не найден!")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Бот запущен (оптимизирован для больших видео)")
    app.run_polling()

if __name__ == "__main__":
    main()
