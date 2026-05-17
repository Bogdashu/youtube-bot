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

# =======================
# TOKEN
# =======================
TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def parse_progress(line):
    match = progress_regex.search(line)

    if match:
        return float(match.group(1))

    return None


# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку — я скачаю видео"
    )


# ---------------- DOWNLOAD ----------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text(
            "❌ Это не YouTube ссылка"
        )
        return

    msg = await update.message.reply_text(
        "⏳ Скачиваю видео..."
    )

    tmpdir = tempfile.mkdtemp(prefix="yt_")

    outtmpl = os.path.join(
        tmpdir,
        "video.%(ext)s"
    )

    # =========================================
    # Сначала пробуем 1080p
    # Если нет -> fallback на 720p
    # Всегда со звуком и видео
    # =========================================
    format_string = (
        # 1080p mp4
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"

        # 1080p любое
        "bestvideo[height<=1080]+bestaudio/"

        # fallback 720 mp4
        "best[height<=720][ext=mp4]/"

        # fallback любое mp4
        "best[ext=mp4]/"

        # самый последний fallback
        "best"
    )

    cmd = [
        "python",
        "-m",
        "yt_dlp",

        "-f",
        format_string,

        "-N",
        "8",

        "--merge-output-format",
        "mp4",

        "--remux-video",
        "mp4",

        "--newline",

        "-o",
        outtmpl,

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
    quality = "1080p"

    try:
        for line in process.stdout:

            # если yt-dlp пишет 720
            if "720" in line:
                quality = "720p"

            percent = parse_progress(line)

            if percent is not None:
                try:
                    await msg.edit_text(
                        f"⏳ Скачиваю видео...\n{percent:.1f}%"
                    )
                except:
                    pass

        process.wait()

        # =========================
        # Ищем видео
        # =========================
        for f in os.listdir(tmpdir):

            path = os.path.join(tmpdir, f)

            if (
                os.path.isfile(path)
                and f.endswith(".mp4")
            ):
                downloaded_file = path
                break

        if not downloaded_file:
            await msg.edit_text(
                "❌ Ошибка: файл не найден"
            )
            return

        # =========================
        # Размер файла
        # =========================
        size_mb = round(
            os.path.getsize(downloaded_file) / 1024 / 1024,
            1
        )

        # Telegram limit
        if size_mb > 49:
            await msg.edit_text(
                f"❌ Видео слишком большое ({size_mb} MB)"
            )
            return

        # =========================
        # Отправка
        # =========================
        await msg.edit_text(
            f"📤 Отправляю видео... ({quality})"
        )

        with open(downloaded_file, "rb") as video:
            await update.message.reply_video(
                video=video,
                caption=f"✅ Готово!\n📦 Размер: {size_mb} MB",
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600,
            )

        await msg.delete()

    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------- MAIN ----------------
def main():

    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Вставь токен!")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    print("🤖 Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
