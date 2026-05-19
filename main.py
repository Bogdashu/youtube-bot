import os
import re
import tempfile
import subprocess

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL")

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def parse_progress(line):
    m = progress_regex.search(line)
    return float(m.group(1)) if m else None


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

        result = subprocess.check_output(
            cmd,
            text=True,
        ).strip()

        return f"{result}p" if result else "unknown"

    except:
        return "unknown"


def estimate_size_mb(url, height):

    try:

        cmd = [
            "yt-dlp",
            "--js-runtimes", "node",
            "--no-playlist",

            "--extractor-args",
            "youtube:player_client=android_vr,web",

            "-f",
            f"(bv*[height<={height}]+ba/b)",

            "--print",
            "%(filesize_approx)s",

            url,
        ]

        out = subprocess.check_output(
            cmd,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

        if not out or out == "NA":
            return None

        return int(out) / 1024 / 1024

    except:
        return None


async def start(update: Update, context):

    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку"
    )


async def handle_message(update: Update, context):

    url = update.message.text.strip()

    if "youtube.com" not in url and "youtu.be" not in url:

        await update.message.reply_text(
            "❌ Это не YouTube ссылка"
        )
        return

    msg = await update.message.reply_text(
        "📥 Подготовка...\n\n"
        "🎞 Проверка качества..."
    )

    target_height = 1080

    est = estimate_size_mb(url, 1080)

    if est and est > 100:
        target_height = 720

    await msg.edit_text(
        f"📥 Скачивание видео...\n\n"
        f"🎞 Цель: {target_height}p\n"
        f"⏳ 0%"
    )

    with tempfile.TemporaryDirectory() as tmpdir:

        outtmpl = os.path.join(
            tmpdir,
            "video.%(ext)s"
        )

        cmd = [
            "yt-dlp",

            "--js-runtimes", "node",

            "--no-playlist",

            "--extractor-args",
            "youtube:player_client=android_vr,web",

            "-N", "4",

            "-f",
            f"(bv*[height<={target_height}]+ba/b)"
            f"/(bv*[height<=720]+ba/b)"
            f"/best",

            "--merge-output-format",
            "mp4",

            "--newline",

            "-o",
            outtmpl,

            url,
        ]

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

            if len(last_lines) > 15:
                last_lines.pop(0)

            percent = parse_progress(line)

            if percent is not None:

                if percent - last_percent >= 5:

                    last_percent = percent

                    try:

                        await msg.edit_text(
                            f"📥 Скачивание видео...\n\n"
                            f"🎞 Цель: {target_height}p\n"
                            f"⏳ {percent:.1f}%"
                        )

                    except:
                        pass

        process.wait()

        if process.returncode != 0:

            err = "".join(last_lines[-5:])[:900]

            await msg.edit_text(
                f"❌ Ошибка yt-dlp\n\n{err}"
            )

            return

        video_file = None

        for f in os.listdir(tmpdir):

            if f.endswith(
                (
                    ".mp4",
                    ".mkv",
                    ".webm",
                )
            ):
                video_file = os.path.join(
                    tmpdir,
                    f,
                )
                break

        if not video_file:

            await msg.edit_text(
                "❌ Видео не найдено"
            )
            return

        real_quality = get_real_resolution(
            video_file
        )

        size_mb = (
            os.path.getsize(video_file)
            / 1024
            / 1024
        )

        await msg.edit_text(
            f"📤 Отправка видео...\n\n"
            f"🎞 {real_quality}\n"
            f"📦 {size_mb:.1f} MB"
        )

        caption = (
            f"✅ Готово\n"
            f"🎞 {real_quality}\n"
            f"📦 {size_mb:.1f} MB"
        )

        with open(video_file, "rb") as v:

            if size_mb <= 49:

                await update.message.reply_video(
                    video=v,
                    caption=caption,
                    supports_streaming=True,
                    read_timeout=1200,
                    write_timeout=1200,
                )

            else:

                await LOCAL_APP.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=v,
                    caption=caption,
                    supports_streaming=True,
                    read_timeout=1200,
                    write_timeout=1200,
                )

        try:
            await msg.delete()
        except:
            pass


NORMAL_APP = (
    Application.builder()
    .token(TOKEN)
    .build()
)

LOCAL_APP = (
    Application.builder()
    .token(TOKEN)
    .base_url(f"{LOCAL_BOT_API_URL}/bot")
    .base_file_url(f"{LOCAL_BOT_API_URL}/file/bot")
    .local_mode(True)
    .build()
)


def main():

    NORMAL_APP.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    NORMAL_APP.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    print("BOT STARTED")

    NORMAL_APP.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
