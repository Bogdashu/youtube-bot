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

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def parse_progress(line):

    match = progress_regex.search(line)

    return float(match.group(1)) if match else None


def get_real_resolution(filepath):

    try:

        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=height",
            "-of",
            "csv=p=0",
            filepath,
        ]

        result = subprocess.check_output(
            cmd,
            text=True,
        ).strip()

        return f"{result}p" if result else "unknown"

    except:
        return "unknown"


def download_video(url, outtmpl, quality):

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-N", "4",
        "--newline",
        "--merge-output-format",
        "mp4",
        "-o",
        outtmpl,
    ]

    if quality == "1080":

        cmd += [
            "-f",
            "bestvideo[height<=1080]+bestaudio/best"
        ]

    else:

        cmd += [
            "-f",
            "bestvideo[height<=720]+bestaudio/best"
        ]

    cmd.append(url)

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


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
        "📥 Скачивание видео...\n\n"
        "🎞 1080p\n"
        "⏳ 0%"
    )

    with tempfile.TemporaryDirectory() as tmpdir:

        outtmpl = os.path.join(
            tmpdir,
            "video.%(ext)s"
        )

        process = download_video(
            url,
            outtmpl,
            "1080"
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
                            f"📥 Скачивание видео...\n\n"
                            f"🎞 1080p\n"
                            f"⏳ {percent:.1f}%"
                        )

                    except:
                        pass

        process.wait()

        if process.returncode != 0:

            error_text = "".join(
                last_lines[-5:]
            )[:900]

            await msg.edit_text(
                f"❌ Ошибка yt-dlp\n\n"
                f"{error_text}"
            )

            return

        video_file = None

        for f in os.listdir(tmpdir):

            if f.endswith((".mp4", ".mkv", ".webm")):

                video_file = os.path.join(
                    tmpdir,
                    f
                )

                break

        if not video_file:

            await msg.edit_text(
                "❌ Видео не найдено"
            )

            return

        size_mb = (
            os.path.getsize(video_file)
            / 1024
            / 1024
        )

        if size_mb > 100:

            await msg.edit_text(
                "📥 Файл слишком большой.\n"
                "Перекодировка в 720p..."
            )

            os.remove(video_file)

            process = download_video(
                url,
                outtmpl,
                "720"
            )

            process.wait()

            video_file = None

            for f in os.listdir(tmpdir):

                if f.endswith(
                    (".mp4", ".mkv", ".webm")
                ):

                    video_file = os.path.join(
                        tmpdir,
                        f
                    )

                    break

            if not video_file:

                await msg.edit_text(
                    "❌ Ошибка 720p"
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

        with open(video_file, "rb") as v:

            caption = (
                f"✅ Готово\n"
                f"🎞 {real_quality}\n"
                f"📦 {size_mb:.1f} MB"
            )

            if size_mb <= 49:

                await update.message.reply_video(
                    video=v,
                    caption=caption,
                    supports_streaming=True,
                    read_timeout=3600,
                    write_timeout=3600,
                )

            else:

                await LOCAL_APP.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=v,
                    caption=caption,
                    supports_streaming=True,
                    read_timeout=3600,
                    write_timeout=3600,
                )

        await msg.delete()


NORMAL_APP = (
    Application.builder()
    .token(TOKEN)
    .concurrent_updates(False)
    .build()
)

LOCAL_APP = (
    Application.builder()
    .token(TOKEN)
    .base_url(f"{LOCAL_BOT_API_URL}/bot")
    .base_file_url(
        f"{LOCAL_BOT_API_URL}/file/bot"
    )
    .local_mode(True)
    .connect_timeout(300)
    .read_timeout(3600)
    .write_timeout(3600)
    .pool_timeout(3600)
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
