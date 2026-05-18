import os
import re
import json
import time
import tempfile
import subprocess
import shutil
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN")

# Регулярка для прогресса скачивания
progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")

def parse_progress(line):
    m = progress_regex.search(line)
    return float(m.group(1)) if m else None

def get_best_format(url):
    """
    Выбирает лучший формат (1080p, если влезет в 45 МБ, иначе 720p).
    Возвращает (format_string, quality_label, estimated_size_mb)
    """
    try:
        # Получаем информацию о видео без скачивания
        cmd = ["yt-dlp", "-J", "--no-playlist", url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return "best[height<=720][ext=mp4]/best", "720p", 0

        info = json.loads(result.stdout)
        formats = info.get("formats", [])

        # Ищем размер 1080p и 720p
        size_1080 = 0
        size_720 = 0

        for f in formats:
            if f.get("vcodec") != "none" and f.get("acodec") != "none":  # готовый mp4
                height = f.get("height", 0)
                fsize = f.get("filesize") or f.get("filesize_approx", 0)
                if height >= 1080:
                    size_1080 = max(size_1080, fsize)
                elif height >= 720:
                    size_720 = max(size_720, fsize)

        size_1080_mb = size_1080 / (1024 * 1024)
        size_720_mb = size_720 / (1024 * 1024)

        # Если 1080p есть и он ≤45 МБ — берём его
        if size_1080_mb > 0 and size_1080_mb <= 45:
            return "best[height<=1080][ext=mp4]/best", "1080p", size_1080_mb
        elif size_720_mb > 0:
            return "best[height<=720][ext=mp4]/best", "720p", size_720_mb
        else:
            return "best[ext=mp4]/best", "best", 0

    except Exception as e:
        print(f"Format selection error: {e}")
        return "best[height<=720][ext=mp4]/best", "720p", 0

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
        "🎬 **YouTube Video Bot**\n\n"
        "Отправь ссылку — скачаю видео.\n"
        "✅ 1080p если размер ≤45 МБ, иначе 720p\n"
        "✅ Большие видео (＞45 МБ) разбиваю на части\n"
        "✅ Обход блокировок YouTube\n\n"
        "Просто отправь ссылку!",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Поддерживаются только YouTube ссылки")
        return

    msg = await update.message.reply_text("📊 Анализирую видео...")

    # Определяем лучший формат
    fmt, quality, est_size = get_best_format(url)

    if est_size > 0:
        await msg.edit_text(f"📊 Выбрано качество: {quality} (~{est_size:.1f} МБ)\n⏳ Скачиваю...")
    else:
        await msg.edit_text(f"⏳ Скачиваю в {quality}...")

    tmpdir = tempfile.mkdtemp(prefix="yt_")
    outtmpl = os.path.join(tmpdir, "%(title)s.%(ext)s")

    # Ключевые параметры для обхода блокировки YouTube
    cmd = [
        "yt-dlp",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--concurrent-fragments", "8",
        "--socket-timeout", "30",
        "--retries", "10",
        "--fragment-retries", "10",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--extractor-args", "youtube:player_client=android,web",
        "--no-check-certificates",
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
        # Отслеживаем прогресс
        for line in process.stdout:
            percent = parse_progress(line)
            if percent is not None:
                try:
                    await msg.edit_text(f"⏳ Скачиваю... {percent:.1f}%")
                except:
                    pass

        process.wait()

        # Ищем скачанный файл
        for f in os.listdir(tmpdir):
            path = os.path.join(tmpdir, f)
            if os.path.isfile(path) and f.endswith(('.mp4', '.mkv', '.webm')):
                video_file = path
                break

        if not video_file:
            await msg.edit_text("❌ Файл не найден. Возможно, видео недоступно или требует авторизации.")
            return

        size_mb = os.path.getsize(video_file) / (1024 * 1024)

        # Если файл ≤45 МБ — отправляем целиком
        if size_mb <= 45:
            await msg.edit_text(f"📤 Отправляю {quality} ({size_mb:.1f} МБ)...")
            with open(video_file, "rb") as v:
                await update.message.reply_video(
                    video=v,
                    caption=f"✅ {quality}\n📊 {size_mb:.1f} МБ",
                    supports_streaming=True,
                    read_timeout=300,
                    write_timeout=300
                )
            await msg.delete()

        # Если файл >45 МБ — разбиваем на части
        else:
            await msg.edit_text(f"📦 Видео {size_mb:.1f} МБ (>45). Разбиваю на части...")
            parts_dir, parts = split_file(video_file, 45)

            # Отправляем инструкцию
            await update.message.reply_text(
                f"📥 **Видео ({quality}) разбито на {len(parts)} частей**\n\n"
                f"**Как собрать (Windows):**\n"
                f"```\ncopy /b part_*.mp4 video.mp4\n```\n\n"
                f"**Как собрать (Linux/Mac):**\n"
                f"```\ncat part_*.mp4 > video.mp4\n```\n\n"
                f"📊 Общий размер: {size_mb:.1f} МБ",
                parse_mode="Markdown"
            )

            # Отправляем части
            for i, part in enumerate(parts, 1):
                part_size = os.path.getsize(part) / (1024 * 1024)
                await msg.edit_text(f"📤 Отправляю часть {i} из {len(parts)} ({part_size:.1f} МБ)...")

                with open(part, "rb") as p:
                    await update.message.reply_document(
                        document=p,
                        filename=f"video_{quality}_part_{i:03d}_{len(parts)}.mp4",
                        caption=f"Часть {i} из {len(parts)}"
                    )
                os.unlink(part)  # Удаляем часть после отправки

            shutil.rmtree(parts_dir, ignore_errors=True)
            await msg.edit_text("✅ Все части отправлены!")
            await msg.delete()

    except subprocess.TimeoutExpired:
        await msg.edit_text("❌ Превышено время ожидания. Попробуйте позже.")
    except Exception as e:
        error_msg = str(e)
        print(f"Error: {error_msg}")
        await msg.edit_text(f"❌ Ошибка: {error_msg[:150]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def main():
    if not TOKEN:
        print("❌ Ошибка: переменная BOT_TOKEN не установлена!")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Бот запущен")
    print("✅ Обход блокировки YouTube активен")
    app.run_polling()

if __name__ == "__main__":
    main()
