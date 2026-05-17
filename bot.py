import os
import re
import tempfile
import subprocess
import shutil
import json
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Токен берется из переменной окружения BOT_TOKEN (НЕ ХРАНИТСЯ В КОДЕ!)
TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    raise ValueError("❌ Ошибка: переменная окружения BOT_TOKEN не установлена!")

progress_regex = re.compile(r'(\d{1,3}(?:\.\d+)?)%')
active_downloads = set()

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_http_server():
    server = HTTPServer(('0.0.0.0', 10000), HealthCheckHandler)
    server.serve_forever()

def parse_progress(line):
    m = progress_regex.search(line)
    return float(m.group(1)) if m else None

def choose_format(url):
    cmd = ["python", "-m", "yt_dlp", "-J", "--no-playlist", url]
    try:
        info = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        info = json.loads(info)
    except subprocess.CalledProcessError:
        return "best[height<=480]/best", "480p", 0
    
    size = 0
    for f in info.get("formats", []):
        if f.get("filesize"):
            size = max(size, f["filesize"])
        elif f.get("filesize_approx"):
            size = max(size, f["filesize_approx"])
    
    size_mb = size / (1024 * 1024)
    
    if size_mb <= 55 and size_mb > 0:
        return "bv*+ba/best", "1080p", size_mb
    else:
        return "best[height<=720]/best", "720p", size_mb

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 Отправь YouTube ссылку — я скачаю видео")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user.id
    url = update.message.text.strip()
    if "youtu" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return
    if len(active_downloads) > 0:
        await update.message.reply_text(f"⏳ Ты в очереди: {len(active_downloads)+1}")
    active_downloads.add(user)
    tmpdir = tempfile.mkdtemp()
    outtmpl = os.path.join(tmpdir, "video.%(ext)s")
    msg = await update.message.reply_text("⏳ Скачиваю видео...")
    try:
        fmt, quality, size_mb = choose_format(url)
        await msg.edit_text(f"⏳ Скачиваю видео... ({size_mb:.1f}MB)")
        cmd = [
            "python", "-m", "yt_dlp",
            "--no-playlist",
            "--concurrent-fragments", "8",
            "--socket-timeout", "20",
            "--retries", "3",
            "--newline",
            "-f", "bestvideo+bestaudio/best",
            "--merge-output-format", "mp4",
            "-o", outtmpl,
            url
        ]
        process = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
        file_path = None
        for line in process.stderr:
            p = parse_progress(line)
            if p is not None:
                try:
                    await msg.edit_text(f"⏳ Скачиваю видео... ({size_mb:.1f}MB) {p:.1f}%")
                except:
                    pass
        process.wait()
        for f in os.listdir(tmpdir):
            if f.endswith(".mp4"):
                file_path = os.path.join(tmpdir, f)
                break
        if not file_path:
            await msg.edit_text("❌ Ошибка загрузки")
            return
        await msg.edit_text(f"📤 Отправляю видео... ({quality})")
        with open(file_path, "rb") as f:
            await update.message.reply_video(video=InputFile(f), caption="✅ Готово")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")
    finally:
        active_downloads.discard(user)
        shutil.rmtree(tmpdir, ignore_errors=True)

def main():
    threading.Thread(target=run_http_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    print("✅ YouTube бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
