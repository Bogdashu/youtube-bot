import os
import re
import tempfile
import subprocess
import shutil
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
        "🎬 Отправь YouTube ссылку — скачаю видео в 1080p\n"
        "✅ Поддерживаются длинные видео\n"
        "⚠️ Для 1080p требуется ffmpeg (установлен на сервере)"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    msg = await update.message.reply_text("⏳ Скачиваю видео в 1080p...")

    tmpdir = tempfile.mkdtemp(prefix="yt_")
    outtmpl = os.path.join(tmpdir, "video.%(ext)s")

    # ============================================
    # ФОРМАТ ДЛЯ 1080p (видео + звук)
    # ============================================
    # bestvideo[height<=1080] - лучшее видео до 1080p
    # +bestaudio[ext=m4a] - лучший звук
    # --merge-output-format mp4 - объединяем в mp4
    format_string = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]+bestaudio/best[height<=1080]/best"

    cmd = [
        "python", "-m", "yt_dlp",
        "-f", format_string,
        "--merge-output-format", "mp4",  # Объединяем видео и аудио в mp4
        "-N", "8",
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

    try:
        for line in process.stdout:
            percent = parse_progress(line)
            if percent is not None:
                try:
                    await msg.edit_text(f"⏳ Скачиваю 1080p...\n{percent:.1f}%")
                except:
                    pass

        process.wait()

        # Ищем видео
        for f in os.listdir(tmpdir):
            path = os.path.join(tmpdir, f)
            if os.path.isfile(path) and (f.endswith(".mp4") or f.endswith(".mkv")):
                downloaded_file = path
                break

        if not downloaded_file:
            await msg.edit_text("❌ Ошибка: файл не найден\nВозможно, у видео нет 1080p")
            return

        file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        
        # Если файл больше 45 MB - разбиваем
        if file_size_mb > 45:
            await msg.edit_text(f"📦 Видео 1080p весит {file_size_mb:.1f} MB\nРазбиваю на части по 45 MB...")
            
            parts_dir, part_paths = split_file(downloaded_file, 45)
            
            await update.message.reply_text(
                f"📥 **Видео 1080p разбито на {len(part_paths)} частей**\n\n"
                f"**Как собрать (Windows):**\n`copy /b part_*.mp4 video.mp4`\n\n"
                f"**Как собрать (Linux/Mac):**\n`cat part_*.mp4 > video.mp4`\n\n"
                f"📊 Общий размер: {file_size_mb:.1f} MB",
                parse_mode="Markdown"
            )
            
            for i, part_path in enumerate(part_paths, 1):
                part_size = os.path.getsize(part_path) / (1024 * 1024)
                await msg.edit_text(f"📤 Отправляю часть {i} из {len(part_paths)}...")
                
                with open(part_path, "rb") as part:
                    await update.message.reply_document(
                        document=part,
                        filename=f"video_1080p_part_{i:03d}_{len(part_paths)}.mp4",
                        caption=f"Часть {i} из {len(part_paths)} ({part_size:.1f} MB)"
                    )
            
            shutil.rmtree(parts_dir, ignore_errors=True)
            await msg.edit_text("✅ Все части 1080p отправлены!")
            await msg.delete()
        else:
            await msg.edit_text(f"📤 Отправляю 1080p... ({file_size_mb:.1f} MB)")

            with open(downloaded_file, "rb") as video:
                await update.message.reply_video(
                    video=video,
                    caption=f"✅ 1080p\n📊 {file_size_mb:.1f} MB",
                    supports_streaming=True,
                    read_timeout=600,
                    write_timeout=600,
                )

            await msg.delete()

    except Exception as e:
        error_msg = str(e)
        if "ffmpeg" in error_msg.lower():
            await msg.edit_text("❌ На сервере нет ffmpeg. Добавьте ffmpeg в настройки Railway")
        else:
            await msg.edit_text(f"❌ Ошибка: {error_msg[:200]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def split_file(file_path, part_size_mb=45):
    """Разбивает файл на части"""
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
            with open(part_path, 'wb') as part_file:
                part_file.write(chunk)
            
            part_paths.append(part_path)
            part_num += 1
    
    return parts_dir, part_paths


# Команда для 720p (если 1080p слишком большое)
async def download_720p(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Укажите ссылку: /720p https://youtube.com/...")
        return
    
    url = context.args[0]
    
    msg = await update.message.reply_text("⏳ Скачиваю 720p...")
    
    tmpdir = tempfile.mkdtemp(prefix="yt_")
    outtmpl = os.path.join(tmpdir, "video.%(ext)s")
    
    format_string = "best[height<=720][ext=mp4]/best[ext=mp4]/best"
    
    cmd = ["python", "-m", "yt_dlp", "-f", format_string, "-N", "8", "--newline", "-o", outtmpl, url]
    
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    
    try:
        for line in process.stdout:
            percent = parse_progress(line)
            if percent:
                try:
                    await msg.edit_text(f"⏳ 720p... {percent:.1f}%")
                except:
                    pass
        
        process.wait()
        
        for f in os.listdir(tmpdir):
            path = os.path.join(tmpdir, f)
            if os.path.isfile(path) and f.endswith(('.mp4', '.mkv')):
                downloaded_file = path
                break
        else:
            await msg.edit_text("❌ Файл не найден")
            return
        
        size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        
        if size_mb > 45:
            await msg.edit_text(f"📦 720p весит {size_mb:.1f} MB, разбиваю...")
            parts_dir, parts = split_file(downloaded_file, 45)
            
            await update.message.reply_text(
                f"📥 **720p разбито на {len(parts)} частей**\n\n"
                f"**Склейка:** `copy /b part_*.mp4 video.mp4`",
                parse_mode="Markdown"
            )
            
            for i, part in enumerate(parts, 1):
                with open(part, "rb") as p:
                    await update.message.reply_document(p, filename=f"720p_part_{i:03d}_{len(parts)}.mp4")
            
            shutil.rmtree(parts_dir, ignore_errors=True)
        else:
            with open(downloaded_file, "rb") as v:
                await update.message.reply_video(v, caption=f"✅ 720p\n📊 {size_mb:.1f} MB")
        
        await msg.delete()
        
    except Exception as e:
        await msg.edit_text(f"❌ {str(e)[:100]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Вставь токен!")
        return

    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("720p", download_720p))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Bot started...")
    print("🎬 Качество по умолчанию: 1080p (с объединением видео+аудио)")
    print("📌 Команда /720p для меньшего качества")
    app.run_polling()


if __name__ == "__main__":
    main()
