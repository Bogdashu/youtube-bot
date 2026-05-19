import os
import re
import json
import tempfile
import subprocess
from threading import Lock

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

PROCESSING_CHATS = set()
STATE_LOCK = Lock()


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

        result = subprocess.check_output(
            cmd,
            text=True,
        ).strip()

        return f"{result}p" if result else "unknown"

    except:
        return "unknown"


def get_video_info(url):
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--extractor-args",
        "youtube:player_client=tv_embedded,tv",
        "--dump-single-json",
        url,
    ]

    try:
        raw = subprocess.check_output(
            cmd,
            text=True,
            stderr=subprocess.STDOUT,
        )
        return json.loads(raw)
    except:
        return None


def format_size_bytes(fmt, duration):
    for key in ("filesize", "filesize_approx"):
        value = fmt.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)

    tbr = fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr")
    if tbr and duration:
        try:
            return float(tbr) * 1000 / 8 * float(duration)
        except:
            return None

    return None


def score_video(fmt):
    return (
        fmt.get("height") or 0,
        fmt.get("tbr") or fmt.get("vbr") or 0,
        fmt.get("filesize") or fmt.get("filesize_approx") or 0,
    )


def score_audio(fmt):
    return (
        fmt.get("abr") or fmt.get("tbr") or 0,
        fmt.get("filesize") or fmt.get("filesize_approx") or 0,
    )


def estimate_size_for_height(info, max_height):
    if not info:
        return None

    formats = info.get("formats") or []
    duration = info.get("duration")

    video_only = [
        f for f in formats
        if f.get("height")
        and f.get("height") <= max_height
        and f.get("vcodec") != "none"
        and f.get("acodec") == "none"
    ]

    progressive = [
        f for f in formats
        if f.get("height")
        and f.get("height") <= max_height
        and f.get("vcodec") != "none"
        and f.get("acodec") != "none"
    ]

    audio_only = [
        f for f in formats
        if f.get("acodec") != "none"
        and f.get("vcodec") == "none"
    ]

    if progressive:
        best = max(progressive, key=score_video)
        return format_size_bytes(best, duration)

    if video_only and audio_only:
        best_video = max(video_only, key=score_video)
        best_audio = max(audio_only, key=score_audio)

        video_size = format_size_bytes(best_video, duration) or 0
        audio_size = format_size_bytes(best_audio, duration) or 0

        total = video_size + audio_size
        return total if total > 0 else None

    if video_only:
        best_video = max(video_only, key=score_video)
        return format_size_bytes(best_video, duration)

    if audio_only:
        best_audio = max(audio_only, key=score_audio)
        return format_size_bytes(best_audio, duration)

    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id

    with STATE_LOCK:
        if chat_id in PROCESSING_CHATS:
            await update.message.reply_text(
                "⏳ Уже обрабатываю предыдущий запрос"
            )
            return
        PROCESSING_CHATS.add(chat_id)

    try:
        url = update.message.text.strip()

        if "youtube.com" not in url and "youtu.be" not in url:
            await update.message.reply_text(
                "❌ Это не YouTube ссылка"
            )
            return

        msg = await update.message.reply_text(
            "📏 Оцениваю размер видео..."
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            outtmpl = os.path.join(
                tmpdir,
                "video.%(ext)s"
            )

            info = get_video_info(url)

            size_1080 = estimate_size_for_height(info, 1080)
            size_720 = estimate_size_for_height(info, 720)

            if size_1080 is not None and size_1080 > 100 * 1024 * 1024:
                target_height = 720
                estimate_text = (
                    f"1080p ≈ {size_1080 / 1024 / 1024:.1f} MB\n"
                    f"↘️ Беру 720p"
                )
            elif size_1080 is not None:
                target_height = 1080
                estimate_text = (
                    f"1080p ≈ {size_1080 / 1024 / 1024:.1f} MB"
                )
            else:
                target_height = 720
                if size_720 is not None:
                    estimate_text = (
                        f"1080p не удалось оценить\n"
                        f"720p ≈ {size_720 / 1024 / 1024:.1f} MB\n"
                        f"↘️ Беру 720p"
                    )
                else:
                    estimate_text = (
                        "Не удалось точно оценить размер\n"
                        "↘️ Беру 720p"
                    )

            await msg.edit_text(
                f"📥 Скачивание видео...\n\n"
                f"🎞 {target_height}p\n"
                f"📦 {estimate_text}\n"
                f"⏳ 0%"
            )

            cmd = [
                "yt-dlp",
                "--no-playlist",
                "--extractor-args",
                "youtube:player_client=tv_embedded,tv",
                "-N", "4",
                "--retries", "10",
                "--fragment-retries", "10",
                "-f",
                f"bv*[height<={target_height}]+ba/b[height<={target_height}]/b",
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

                if len(last_lines) > 10:
                    last_lines.pop(0)

                percent = parse_progress(line)

                if percent is not None and percent - last_percent >= 5:
                    last_percent = percent
                    try:
                        await msg.edit_text(
                            f"📥 Скачивание видео...\n\n"
                            f"🎞 {target_height}p\n"
                            f"📦 {estimate_text}\n"
                            f"⏳ {percent:.1f}%"
                        )
                    except:
                        pass

            process.wait()

            if process.returncode != 0:
                error_text = "".join(last_lines[-3:])[:500]
                await msg.edit_text(
                    f"❌ Ошибка yt-dlp\n\n"
                    f"{error_text}"
                )
                return

            video_file = None

            for f in os.listdir(tmpdir):
                if f.endswith((".mp4", ".mkv", ".webm")):
                    video_file = os.path.join(tmpdir, f)
                    break

            if not video_file:
                await msg.edit_text(
                    "❌ Видео не найдено"
                )
                return

            real_quality = get_real_resolution(video_file)

            size_mb = os.path.getsize(video_file) / 1024 / 1024

            await msg.edit_text(
                f"📤 Отправка видео...\n\n"
                f"🎞 {real_quality}\n"
                f"📦 {size_mb:.1f} MB"
            )

            with open(video_file, "rb") as v:
                if size_mb <= 49:
                    await update.message.reply_video(
                        video=v,
                        caption=(
                            f"✅ Готово\n"
                            f"🎞 {real_quality}\n"
                            f"📦 {size_mb:.1f} MB"
                        ),
                        supports_streaming=True,
                        read_timeout=1200,
                        write_timeout=1200,
                    )
                else:
                    await LOCAL_APP.bot.send_video(
                        chat_id=chat_id,
                        video=v,
                        caption=(
                            f"✅ Готово\n"
                            f"🎞 {real_quality}\n"
                            f"📦 {size_mb:.1f} MB"
                        ),
                        supports_streaming=True,
                        read_timeout=1200,
                        write_timeout=1200,
                    )

            try:
                await msg.delete()
            except:
                pass

    finally:
        with STATE_LOCK:
            PROCESSING_CHATS.discard(chat_id)


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
