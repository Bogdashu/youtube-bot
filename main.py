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
        "🎬 Отправь YouTube ссылку — я скачаю видео в 1080p"
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
        "⏳ Скачиваю видео в 1080p..."
    )

    tmpdir = tempfile.mkdtemp(prefix="yt_")

    outtmpl = os.path.join(
        tmpdir,
        "video.%(ext)s"
    )

    # =========================
    # 1080p + звук + видео
    # =========================
    format_string = (
        "best[height<=1080][ext=mp4]/"
        "best[ext=mp4]/"
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

    try:
        for line in process.stdout:
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
                and (
                    f.endswith(".mp4")
                    or f.endswith(".mkv")
                    or f.endswith(".webm")
                )
            ):
                downloaded_file = path
                break

        if not downloaded_file:
            await msg.edit_text(
                "❌ Ошибка: файл не найден"
            )
            return

        # Проверяем размер файла
        file_size_mb = os.path.getsize(downloaded_file) / (1024 * 1024)
        
        # Если файл больше 50 MB, предупреждаем
        if file_size_mb > 50:
            await msg.edit_text(
                f"⚠️ Видео весит {file_size_mb:.1f} MB\n"
                f"Telegram может не принять файлы больше 50 MB\n\n"
                f"Попробую отправить..."
            )

        # =========================
        # Отправка
        # =========================
        await msg.edit_text(
            f"📤 Отправляю видео... (1080p, {file_size_mb:.1f} MB)"
        )

        with open(downloaded_file, "rb") as video:
            await update.message.reply_video(
                video=video,
                caption="✅ Готово! (1080p)",
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
    print("✅ Качество: 1080p")
    app.run_polling()


if __name__ == "__main__":
    main()
