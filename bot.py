import os
import re
import tempfile
import subprocess
import shutil
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = "8653225245:AAHRkN8UTXPYHMYpwB9fpRR4ICHB13Sckxc"

progress_regex = re.compile(r'(\d{1,3}(?:\.\d+)?)%')
active_downloads = set()


def parse_progress(line):
    m = progress_regex.search(line)
    return float(m.group(1)) if m else None


def format_size(bytes_):
    if not bytes_:
        return "?"
    return f"{bytes_ / (1024 * 1024):.1f}MB"


def choose_format(url):
    """
    ≤55MB → BEST (1080p)
    >55MB → 720p
    """
    cmd = ["python", "-m", "yt_dlp", "-J", "--no-playlist", url]
    info = subprocess.check_output(cmd, text=True)
    import json
    info = json.loads(info)

    size = 0
    for f in info.get("formats", []):
        if f.get("filesize"):
            size = max(size, f["filesize"])

    size_mb = size / (1024 * 1024)

    if size_mb <= 55:
        return "bv*+ba/best", "1080p", size_mb
    else:
        return "bv*[height<=720]+ba/best[height<=720]", "720p", size_mb


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

        await msg.edit_text(
            f"⏳ Скачиваю видео... ({size_mb:.1f}MB)\n"
        )

        cmd = [
            "python", "-m", "yt_dlp",
            "--no-playlist",
            "--concurrent-fragments", "8",
            "--socket-timeout", "20",
            "--retries", "3",
            "--newline",
            "-f", fmt,
            "--merge-output-format", "mp4",
            "-o", outtmpl,
            url
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        file_path = None

        for line in process.stderr:
            p = parse_progress(line)
            if p is not None:
                try:
                    await msg.edit_text(
                        f"⏳ Скачиваю видео... ({size_mb:.1f}MB) {p:.1f}%"
                    )
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
            await update.message.reply_video(
                video=InputFile(f),
                caption="✅ Готово"
            )

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

    finally:
        active_downloads.discard(user)
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    print("bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
