import os
import re
import tempfile
import subprocess
from telegram import Update, Bot
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

processing = set()


def parse_progress(line):
    match = progress_regex.search(line)
    return float(match.group(1)) if match else None


def get_real_resolution(filepath):
    try:
        cmd = [
            "ffprobe",
            "-v","error",
            "-select_streams","v:0",
            "-show_entries","stream=height",
            "-of","csv=p=0",
            filepath
        ]

        result = subprocess.check_output(
            cmd,
            text=True
        ).strip()

        return f"{result}p" if result else "unknown"

    except:
        return "unknown"


async def start(update: Update, context):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку"
    )


async def handle_message(update: Update, context):

    chat_id = update.effective_chat.id

    if chat_id in processing:
        await update.message.reply_text(
            "⏳ Уже скачиваю предыдущее видео"
        )
        return

    processing.add(chat_id)

    try:

        url = update.message.text.strip()

        if "youtube.com" not in url and "youtu.be" not in url:
            await update.message.reply_text(
                "❌ Это не YouTube ссылка"
            )
            return

        msg = await update.message.reply_text(
            "📥 Скачивание видео...\n\n"
            "🎞 Подготовка...\n"
            "⏳ 0%"
        )

        with tempfile.TemporaryDirectory() as tmpdir:

            outtmpl = os.path.join(
                tmpdir,
                "video.%(ext)s"
            )

            cmd = [
                "yt-dlp",

                "--no-playlist",

                "--extractor-args",
                "youtube:player_client=tv_embedded,tv",

                "-f",
                "(bestvideo[height<=1080]+bestaudio)/(bestvideo[height<=720]+bestaudio)/best",

                "--merge-output-format",
                "mp4",

                "--newline",

                "-N","4",

                "--retries","10",
                "--fragment-retries","10",

                "-o",
                outtmpl,

                url,
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
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
                                f"🎞 Подготовка...\n"
                                f"⏳ {percent:.1f}%"
                            )

                        except:
                            pass

            process.wait()

            if process.returncode != 0:

                err = "".join(
                    last_lines[-5:]
                )[:1000]

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
                        ".webm"
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
                /1024
                /1024
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

            if size_mb <= 49:

                with open(video_file,"rb") as v:

                    await update.message.reply_video(
                        video=v,
                        caption=caption,
                        supports_streaming=True,
                        read_timeout=1200,
                        write_timeout=1200,
                    )

            else:

                local_bot = Bot(
                    token=TOKEN,
                    base_url=f"{LOCAL_BOT_API_URL}/bot",
                    base_file_url=f"{LOCAL_BOT_API_URL}/file/bot",
                    local_mode=True,
                )

                with open(video_file,"rb") as v:

                    await local_bot.send_video(
                        chat_id=chat_id,
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

    finally:

        processing.discard(chat_id)


def main():

    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    print("BOT STARTED")

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
