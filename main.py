import asyncio
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


def normalize_url(value):
    if not value:
        return ""
    return value.rstrip("/")


TOKEN = os.getenv("BOT_TOKEN", "")
LOCAL_BOT_API_URL = normalize_url(os.getenv("LOCAL_BOT_API_URL", ""))
WORKER_URL = normalize_url(os.getenv("WORKER_URL", ""))
WORKER_SECRET = os.getenv("WORKER_SECRET", "")
REMOTE_MIN_MB = float(os.getenv("REMOTE_MIN_MB", "120"))
FORCE_REMOTE = os.getenv("FORCE_REMOTE", "0") == "1"
STATUS_POLL_SECONDS = float(os.getenv("STATUS_POLL_SECONDS", "3"))

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


def parse_progress(line):
    match = progress_regex.search(line)
    return float(match.group(1)) if match else None


def is_youtube_url(text):
    return "youtube.com" in text or "youtu.be" in text


def estimate_size_mb(url, height):
    try:
        cmd = [
            "yt-dlp",
            "--js-runtimes",
            "node",
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
            timeout=120,
        ).strip()
        if not out or out == "NA":
            return None
        return int(out) / 1024 / 1024
    except Exception:
        return None


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
        result = subprocess.check_output(cmd, text=True).strip()
        return f"{result}p" if result else "unknown"
    except Exception:
        return "unknown"


async def safe_edit(message, text):
    try:
        await message.edit_text(text)
    except Exception:
        pass


def should_use_worker(estimated_mb):
    if not WORKER_URL:
        return False
    if FORCE_REMOTE:
        return True
    if estimated_mb is None:
        return True
    return estimated_mb >= REMOTE_MIN_MB


def choose_target_height(estimated_mb):
    if estimated_mb and estimated_mb > 100:
        return 720
    return 1080


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отправь YouTube ссылку.")


async def run_remote_job(update, msg, url, target_height):
    if not WORKER_SECRET:
        await safe_edit(msg, "WORKER_SECRET не задан в Railway variables.")
        return True

    headers = {"X-Worker-Secret": WORKER_SECRET}
    payload = {
        "url": url,
        "chat_id": update.effective_chat.id,
        "target_height": target_height,
    }

    timeout = httpx.Timeout(30.0, connect=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{WORKER_URL}/jobs", json=payload, headers=headers)
            response.raise_for_status()
            job_id = response.json()["job_id"]

            last_text = ""
            while True:
                await asyncio.sleep(STATUS_POLL_SECONDS)
                status_response = await client.get(f"{WORKER_URL}/jobs/{job_id}", headers=headers)
                status_response.raise_for_status()
                job = status_response.json()

                state = job.get("state", "unknown")
                progress = job.get("progress", 0)
                target = job.get("target_height", target_height)
                size_mb = job.get("size_mb")
                detail = job.get("detail", "")

                if state == "queued":
                    text = "Задача отправлена на сервер. Ожидание..."
                elif state == "downloading":
                    text = f"Скачивание на сервере...\n\nЦель: {target}p\nПрогресс: {progress:.1f}%"
                elif state == "sending":
                    size_text = f"\nРазмер: {size_mb:.1f} MB" if size_mb else ""
                    text = f"Отправка видео через Telegram Bot API...{size_text}"
                elif state == "done":
                    await safe_edit(msg, "Готово. Видео отправлено.")
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                    return True
                elif state == "failed":
                    await safe_edit(msg, f"Ошибка на сервере:\n\n{detail[:900]}")
                    return False
                else:
                    text = f"Статус сервера: {state}"

                if text != last_text:
                    await safe_edit(msg, text)
                    last_text = text

    except Exception as exc:
        await safe_edit(msg, f"Не удалось выполнить задачу на сервере:\n\n{exc}")
        return False


async def send_video_file(update, video_file, caption, size_mb):
    with open(video_file, "rb") as video:
        if size_mb <= 49 or LOCAL_APP is None:
            if size_mb > 49 and LOCAL_APP is None:
                raise RuntimeError("LOCAL_BOT_API_URL не задан, файл больше 49 MB нельзя отправить через cloud Bot API.")
            await update.message.reply_video(
                video=video,
                caption=caption,
                supports_streaming=True,
                read_timeout=1200,
                write_timeout=1200,
            )
        else:
            await LOCAL_APP.bot.send_video(
                chat_id=update.effective_chat.id,
                video=video,
                caption=caption,
                supports_streaming=True,
                read_timeout=1200,
                write_timeout=1200,
            )


async def download_local_and_send(update, msg, url, target_height):
    await safe_edit(
        msg,
        f"Скачивание на Railway...\n\nЦель: {target_height}p\nПрогресс: 0%",
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        outtmpl = str(Path(tmpdir) / "video.%(ext)s")
        cmd = [
            "yt-dlp",
            "--js-runtimes",
            "node",
            "--no-playlist",
            "--extractor-args",
            "youtube:player_client=android_vr,web",
            "-N",
            "4",
            "-f",
            f"(bv*[height<={target_height}]+ba/b)/(bv*[height<=720]+ba/b)/best",
            "--merge-output-format",
            "mp4",
            "--newline",
            "-o",
            outtmpl,
            url,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        last_percent = -5
        last_lines = []

        while True:
            raw_line = await process.stdout.readline()
            if not raw_line:
                break

            line = raw_line.decode(errors="replace")
            last_lines.append(line)
            if len(last_lines) > 15:
                last_lines.pop(0)

            percent = parse_progress(line)
            if percent is not None and percent - last_percent >= 5:
                last_percent = percent
                await safe_edit(
                    msg,
                    f"Скачивание на Railway...\n\nЦель: {target_height}p\nПрогресс: {percent:.1f}%",
                )

        return_code = await process.wait()
        if return_code != 0:
            err = "".join(last_lines[-5:])[:900]
            await safe_edit(msg, f"Ошибка yt-dlp:\n\n{err}")
            return False

        video_file = None
        for item in Path(tmpdir).iterdir():
            if item.suffix.lower() in {".mp4", ".mkv", ".webm"}:
                video_file = str(item)
                break

        if not video_file:
            await safe_edit(msg, "Видео не найдено после скачивания.")
            return False

        real_quality = get_real_resolution(video_file)
        size_mb = os.path.getsize(video_file) / 1024 / 1024

        await safe_edit(
            msg,
            f"Отправка видео...\n\nКачество: {real_quality}\nРазмер: {size_mb:.1f} MB",
        )

        caption = f"Готово\nКачество: {real_quality}\nРазмер: {size_mb:.1f} MB"
        await send_video_file(update, video_file, caption, size_mb)

        try:
            await msg.delete()
        except Exception:
            pass

        return True


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not is_youtube_url(url):
        await update.message.reply_text("Это не YouTube ссылка.")
        return

    msg = await update.message.reply_text("Подготовка...\n\nПроверка размера и качества.")

    estimated_mb = estimate_size_mb(url, 1080)
    target_height = choose_target_height(estimated_mb)

    if should_use_worker(estimated_mb):
        ok = await run_remote_job(update, msg, url, target_height)
        if ok:
            return
        await safe_edit(msg, "Сервер не справился. Пробую Railway fallback...")

    await download_local_and_send(update, msg, url, target_height)


if not TOKEN:
    raise RuntimeError("BOT_TOKEN is required.")

NORMAL_APP = Application.builder().token(TOKEN).concurrent_updates(True).build()

LOCAL_APP = None
if LOCAL_BOT_API_URL:
    LOCAL_APP = (
        Application.builder()
        .token(TOKEN)
        .base_url(f"{LOCAL_BOT_API_URL}/bot")
        .base_file_url(f"{LOCAL_BOT_API_URL}/file/bot")
        .local_mode(True)
        .build()
    )


def main():
    NORMAL_APP.add_handler(CommandHandler("start", start))
    NORMAL_APP.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("BOT STARTED")
    NORMAL_APP.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

