import os
import re
import tempfile
import subprocess
from threading import Lock

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
YT_COOKIES = os.getenv("YT_COOKIES")

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")

PROCESSING_CHATS = set()
STATE_LOCK = Lock()


def parse_progress(line: str):
    match = progress_regex.search(line)
    return float(match.group(1)) if match else None


def get_real_resolution(filepath: str) -> str:
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
    except Exception:
        return "unknown"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 Отправь YouTube ссылку")


async def send_via_local_bot(chat_id: int, video_path: str, caption: str):
    async with Bot(
        token=TOKEN,
        base_url=f"{LOCAL_BOT_API_URL}/bot",
        base_file_url=f"{LOCAL_BOT_API_URL}/file/bot",
        local_mode=True,
    ) as local_bot:
        with open(video_path, "rb") as v:
            await local_bot.send_video(
                chat_id=chat_id,
                video=v,
                caption=caption,
                supports_streaming=True,
                read_timeout=1200,
                write_timeout=1200,
                connect_timeout=1200,
                pool_timeout=1200,
            )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    url = (update.message.text or "").strip()

    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    with STATE_LOCK:
        if chat_id in PROCESSING_CHATS:
            await update.message.reply_text("⏳ Уже обрабатываю предыдущий запрос.")
            return
        PROCESSING_CHATS.add(chat_id)

    msg = None

    try:
        msg = await update.message.reply_text(
            "📥 Скачивание видео...\n\n"
            "🎞 Подготовка...\n"
            "⏳ 0%"
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
                "--extractor-args",
                "youtube:player_client=mweb",
                "-f",
                "bv*+ba/b[ext=mp4]/b",
                "--format-sort",
                "res,ext:mp4:m4a",
                "-N", "8",
                "--merge-output-format", "mp4",
                "--newline",
                "-o", outtmpl,
                url,
            ]
            if cookies_path:
                cmd += ["--cookies", cookies_path]

            cmd.append(url)

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
                if len(last_lines) > 10:
                    last_lines.pop(0)

                percent = parse_progress(line)
                if percent is not None and percent - last_percent >= 5:
                    last_percent = percent
                    try:
                        await msg.edit_text(
                            f"📥 Скачивание видео...\n\n"
                            f"🎞 Подготовка...\n"
                            f"⏳ {percent:.1f}%"
                        )
                    except Exception:
                        pass

            process.wait()

            if process.returncode != 0:
                error_text = "".join(last_lines[-3:])[:1000]
                await msg.edit_text(f"❌ Ошибка yt-dlp\n\n{error_text}")
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
                with open(video_file, "rb") as v:
                    await update.message.reply_video(
                        video=v,
                        caption=caption,
                        supports_streaming=True,
                        read_timeout=1200,
                        write_timeout=1200,
                        connect_timeout=1200,
                        pool_timeout=1200,
                    )
            else:
                await send_via_local_bot(
                    chat_id=chat_id,
                    video_path=video_file,
                    caption=caption,
                )

            try:
                await msg.delete()
            except Exception:
                pass

    finally:
        with STATE_LOCK:
            PROCESSING_CHATS.discard(chat_id)


def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(1200)
        .read_timeout(1200)
        .write_timeout(1200)
        .pool_timeout(1200)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("BOT STARTED")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
