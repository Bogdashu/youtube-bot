import os
import re
import tempfile
import subprocess
import shutil
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN")

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")

def parse_progress(line):
    m = progress_regex.search(line)
    return float(m.group(1)) if m else None

def split_file(file_path, part_size_mb=45):
    """Разбивает файл на части по 45 MB"""
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
        "✅ Видео до 45 МБ → приходит целиком\n"
        "✅ Большие видео → разбиваются на части\n"
        "🛡️ Обход блокировки YouTube активен"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    
    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Не YouTube ссылка")
        return
    
    msg = await update.message.reply_text("⏳ Подготовка к скачиванию...")
    tmpdir = tempfile.mkdtemp(prefix="yt_")
    outtmpl = os.path.join(tmpdir, "video.%(ext)s")
    
    # ============================================
    # ОПТИМИЗИРОВАННЫЕ ПАРАМЕТРЫ ДЛЯ ОБХОДА БЛОКИРОВКИ
    # ============================================
    cmd = [
        "yt-dlp",  # Используем напрямую, не через python -m
        "-f", "best[height<=720][ext=mp4]/best[height<=720]/best",
        "-N", "8",
        "--newline",
        "-o", outtmpl,
        "--no-mux",
        "--force-overwrites",
        "--no-part",
        "--retries", "10",
        "--fragment-retries", "10",
        "--socket-timeout", "60",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--extractor-args", "youtube:player_client=android,web",
        "--sleep-requests", "1",
        "--sleep-interval", "5",
        "--max-sleep-interval", "10",
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
        last_percent = 0
        for line in process.stdout:
            percent = parse_progress(line)
            if percent is not None:
                if percent - last_percent >= 10:  # Обновляем каждые 10%
                    last_percent = percent
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
            # Пробуем найти любой файл
            for f in os.listdir(tmpdir):
                path = os.path.join(tmpdir, f)
                if os.path.isfile(path):
                    video_file = path
                    break
        
        if not video_file:
            await msg.edit_text("❌ Файл не найден. YouTube блокирует загрузку.\n\nПопробуйте:\n1. Подождать 5-10 минут\n2. Использовать другое видео\n3. Скачать видео самим через yt-dlp")
            return
        
        size_mb = os.path.getsize(video_file) / (1024 * 1024)
        
        if size_mb > 45:
            await msg.edit_text(f"📦 Видео {size_mb:.1f} MB, разбиваю на части...")
            parts_dir, parts = split_file(video_file, 45)
            
            await update.message.reply_text(
                f"📥 **Видео разбито на {len(parts)} частей**\n\n"
                f"**Как собрать (Windows):**\n`copy /b part_*.mp4 video.mp4`\n\n"
                f"**Как собрать (Linux/Mac):**\n`cat part_*.mp4 > video.mp4`\n\n"
                f"📊 Общий размер: {size_mb:.1f} MB",
                parse_mode="Markdown"
            )
            
            for i, part in enumerate(parts, 1):
                part_size = os.path.getsize(part) / (1024 * 1024)
                await msg.edit_text(f"📤 Отправляю часть {i} из {len(parts)} ({part_size:.1f} MB)...")
                
                with open(part, "rb") as p:
                    await update.message.reply_document(
                        document=p, 
                        filename=f"video_part_{i:03d}_{len(parts)}.mp4"
                    )
                os.unlink(part)
            
            shutil.rmtree(parts_dir, ignore_errors=True)
            await msg.edit_text("✅ Все части отправлены!")
            await msg.delete()
        else:
            await msg.edit_text(f"📤 Отправляю ({size_mb:.1f} MB)...")
            with open(video_file, "rb") as v:
                await update.message.reply_video(
                    video=v, 
                    caption=f"✅ {size_mb:.1f} MB",
                    supports_streaming=True,
                    read_timeout=300,
                    write_timeout=300
                )
            await msg.delete()
            
    except Exception as e:
        error_msg = str(e)
        print(f"Error: {error_msg}")
        await msg.edit_text(f"❌ Ошибка: {error_msg[:100]}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def main():
    if not TOKEN:
        print("❌ BOT_TOKEN не найден!")
        return
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🤖 Бот запущен")
    print("🛡️ Антиблокировка YouTube активна")
    app.run_polling()

if __name__ == "__main__":
    main()
