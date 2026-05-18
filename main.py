import os
import re
import tempfile
import subprocess

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL")
YT_COOKIES = os.getenv("YT_COOKIES")

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def parse_progress(line):
    match = progress_regex.search(line)
    return float(match.group(1)) if match else None


def get_real_resolution(filepath):
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=height",
            "-of", "csv=p=0",
            filepath,
        ]

        result = subprocess.check_output(cmd, text=True).strip()
        return f"{result}p" if result else "unknown"

    except:
        return "unknown"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 Отправь YouTube ссылку")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = update.message.text.strip()

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    msg = await update.message.reply_text(
        "📥 Скачивание...\n🎞 Подготовка...\n⏳ 0%"
    )

    with tempfile.TemporaryDirectory() as tmpdir:

        outtmpl = os.path.join(tmpdir, "video.%(ext)s")

        cookies_path = None

        if YT_COOKIES:
            cookies_path = os.path.join(tmpdir, "cookies.txt")
            with open(cookies_path, "w", encoding="utf-8") as f:
                f.write(YT_COOKIES)

        cmd = [
            "yt-dlp",

            "--no-playlist",

            # СТАБИЛЬНЫЙ клиент (без android)
            "--extractor-args",
            "youtube:player_client=ios",

            # fallback чтобы не падало
            "-f",
            "bv*+ba/b[ext=mp4]/b",

            "--format-sort",
            "res,ext:mp4:m4a",

            "-N", "4",

            "--merge-output-format",
            "mp4",

            "--newline",

            "-o",
            outtmpl,
        ]

        if cookies_path:
            cmd += ["--cookies", cookies_path]

        cmd.append(url)

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        last_percent = -5
        last_lines = []

        for line in process.stdout:

            last_lines.append(line)
            if len(last_lines) > 10:
                last_lines.pop(0)

            percent = parse_progress(line)

            if percent is not None:

                if percent - last_percent >= 5:
                    last_percent = percent

                    try:
                        await msg.edit_text(
                            f"📥 Скачивание...\n🎞 Подготовка...\n⏳ {percent:.1f}%"
                        )
                    except:
                        pass

        process.wait()

        if process.returncode != 0:

            error_text = "".join(last_lines[-3:])[:800]

            await msg.edit_text(
                f"❌ Ошибка yt-dlp\n\n{error_text}"
            )
            return

        video_file = None

        for f in os.listdir(tmpdir):
            if f.endswith((".mp4", ".mkv", ".webm")):
                video_file = os.path.join(tmpdir, f)
                break

        if not video_file:
            await msg.edit_text("❌ Видео не найдено")
            return

        real_quality = get_real_resolution(video_file)

        size_mb = os.path.getsize(video_file) / 1024 / 1024

        await msg.edit_text(
            f"📤 Отправка...\n🎞 {real_quality}\n📦 {size_mb:.1f} MB"
        )

        with open(video_file, "rb") as v:

            if size_mb <= 49:

                await update.message.reply_video(
                    video=v,
                    caption=f"✅ Готово\n🎞 {real_quality}\n📦 {size_mb:.1f} MB",
                    supports_streaming=True,
                    read_timeout=1200,
                    write_timeout=1200,
                )

            else:

                await LOCAL_APP.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=v,
                    caption=f"✅ Готово\n🎞 {real_quality}\n📦 {size_mb:.1f} MB",
                    supports_streaming=True,
                    read_timeout=1200,
                    write_timeout=1200,
                )

        await msg.delete()


NORMAL_APP = (
    Application.builder()
    .token(TOKEN)
    .connect_timeout(1200)
    .read_timeout(1200)
    .write_timeout(1200)
    .pool_timeout(1200)
    .build()
)

LOCAL_APP = (
    Application.builder()
    .token(TOKEN)
    .base_url(f"{LOCAL_BOT_API_URL}/bot")
    .base_file_url(f"{LOCAL_BOT_API_URL}/file/bot")
    .local_mode(True)
    .connect_timeout(1200)
    .read_timeout(1200)
    .write_timeout(1200)
    .pool_timeout(1200)
    .build()
)


def main():
    NORMAL_APP.add_handler(CommandHandler("start", start))
    NORMAL_APP.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("BOT STARTED")

    NORMAL_APP.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
