import asyncio
import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

TOKEN = os.getenv("BOT_TOKEN", "")
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL", "").rstrip("/")
WORKER_URL = os.getenv("WORKER_URL", "").rstrip("/")
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "")

LOCAL_MAX_MB = float(os.getenv("LOCAL_MAX_MB", "49"))
WORKER_TRIGGER_MB = float(os.getenv("WORKER_TRIGGER_MB", "80"))
DEFAULT_HEIGHT = int(os.getenv("DEFAULT_HEIGHT", "1080"))
LOWER_HEIGHT = int(os.getenv("LOWER_HEIGHT", "720"))

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")
youtube_regex = re.compile(r"(youtube\.com|youtu\.be)", re.I)


def is_youtube_url(text: str) -> bool:
    return bool(youtube_regex.search(text))


def parse_progress(line: str) -> Optional[float]:
    m = progress_regex.search(line)
    return float(m.group(1)) if m else None


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


def estimate_size_mb_local(url: str, height: int) -> Optional[float]:
    try:
        cmd = [
            "yt-dlp",
            "--js-runtimes",
            "node",
            "--no-playlist",
            "--extractor-args",
            "youtube:player_client=android_vr,web",
            "--print",
            "%(filesize_approx)s",
            "-f",
            f"(bv*[height<={height}]+ba/b)",
            url,
        ]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        if not out or out == "NA":
            return None
        return int(out) / 1024 / 1024
    except Exception:
        return None


async def estimate_size_mb_remote(url: str) -> dict:
    if not WORKER_URL or not WORKER_TOKEN:
        return {"ok": False, "error": "worker is not configured"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(40.0)) as client:
        r = await client.post(
            f"{WORKER_URL}/estimate",
            json={"url": url},
            headers={"X-Worker-Token": WORKER_TOKEN},
        )
        r.raise_for_status()
        return r.json()


async def local_download(url: str, target_height: int, msg) -> Path:
    with tempfile.TemporaryDirectory() as tmpdir:
        outtmpl = os.path.join(tmpdir, "video.%(ext)s")

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

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        last_percent = -5.0
        last_lines: list[str] = []

        assert process.stdout is not None
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break

            line = raw.decode("utf-8", "replace").rstrip("\n")
            last_lines.append(line)
            if len(last_lines) > 15:
                last_lines.pop(0)

            percent = parse_progress(line)
            if percent is not None and percent - last_percent >= 5:
                last_percent = percent
                try:
                    await msg.edit_text(
                        f"📥 Скачивание видео...\n\n"
                        f"🎞 Цель: {target_height}p\n"
                        f"⏳ {percent:.1f}%"
                    )
                except Exception:
                    pass

        rc = await process.wait()
        if rc != 0:
            err = "\n".join(last_lines[-6:])[:1200]
            raise RuntimeError(err or f"yt-dlp exited with code {rc}")

        final_file = None
        for ext in (".mp4", ".mkv", ".webm", ".mov"):
            candidates = list(Path(tmpdir).glob(f"video*{ext}"))
            if candidates:
                candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
                final_file = candidates[0]
                break

        if final_file is None:
            candidates = list(Path(tmpdir).glob("video.*"))
            if candidates:
                candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
                final_file = candidates[0]

        if final_file is None or not final_file.exists():
            raise RuntimeError("Видео не найдено после скачивания")

        dest = Path(tempfile.gettempdir()) / f"railway_video_{os.getpid()}_{final_file.name}"
        shutil.copy2(final_file, dest)
        return dest


async def worker_download(url: str, target_height: int, msg) -> Path:
    if not WORKER_URL or not WORKER_TOKEN:
        raise RuntimeError("WORKER_URL or WORKER_TOKEN is missing")

    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=30.0)) as client:
        create = await client.post(
            f"{WORKER_URL}/jobs",
            json={"url": url, "target_height": target_height},
            headers={"X-Worker-Token": WORKER_TOKEN},
        )
        create.raise_for_status()
        job_id = create.json()["job_id"]

        try:
            last_status = None
            while True:
                status_resp = await client.get(
                    f"{WORKER_URL}/jobs/{job_id}",
                    headers={"X-Worker-Token": WORKER_TOKEN},
                )
                status_resp.raise_for_status()
                data = status_resp.json()

                status = data["status"]
                progress = float(data.get("progress") or 0.0)

                if (status, progress) != last_status:
                    last_status = (status, progress)

                    if status == "queued":
                        label = "В очереди..."
                    elif status == "downloading":
                        label = (
                            f"Скачивание на сервере РФ...\n\n"
                            f"🎞 Цель: {target_height}p\n"
                            f"⏳ {progress:.1f}%"
                        )
                    elif status == "merging":
                        label = (
                            f"Скачивание на сервере РФ...\n\n"
                            f"🎞 Цель: {target_height}p\n"
                            f"🛠 Сборка..."
                        )
                    elif status == "done":
                        label = "Файл готов, получаю его..."
                    elif status == "error":
                        label = f"Ошибка на worker:\n\n{data.get('error', '')[:900]}"
                    else:
                        label = "Работаю..."

                    try:
                        await msg.edit_text(label)
                    except Exception:
                        pass

                if status == "done":
                    break
                if status == "error":
                    raise RuntimeError(data.get("error") or "worker error")

                await asyncio.sleep(2)

            file_resp = await client.get(
                f"{WORKER_URL}/jobs/{job_id}/file",
                headers={"X-Worker-Token": WORKER_TOKEN},
                timeout=None,
            )
            file_resp.raise_for_status()

            tmp_path = Path(tempfile.gettempdir()) / f"worker_{job_id}.mp4"
            with open(tmp_path, "wb") as f:
                async for chunk in file_resp.aiter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)

            return tmp_path
        finally:
            with contextlib.suppress(Exception):
                await client.delete(
                    f"{WORKER_URL}/jobs/{job_id}",
                    headers={"X-Worker-Token": WORKER_TOKEN},
                )


async def start(update: Update, context):
    await update.message.reply_text("🎬 Отправь YouTube ссылку")


async def handle_message(update: Update, context):
    text = (update.message.text or "").strip()
    if not is_youtube_url(text):
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    msg = await update.message.reply_text(
        "📥 Подготовка...\n\n"
        "🔎 Проверяю размер и решаю, где скачивать..."
    )

    target_height = DEFAULT_HEIGHT
    use_worker = False

    try:
        est = await estimate_size_mb_remote(text)
        if est.get("ok"):
            estimated_1080_mb = est.get("estimated_1080_mb")
            recommended_height = int(est.get("recommended_height") or DEFAULT_HEIGHT)
            target_height = recommended_height

            if estimated_1080_mb is None:
                use_worker = True
            else:
                estimated_1080_mb = float(estimated_1080_mb)
                use_worker = estimated_1080_mb > WORKER_TRIGGER_MB

            if estimated_1080_mb is not None:
                await msg.edit_text(
                    f"📥 Подготовка...\n\n"
                    f"🎞 Цель: {target_height}p\n"
                    f"📦 Оценка 1080p: {estimated_1080_mb:.1f} MB"
                )
            else:
                await msg.edit_text(
                    f"📥 Подготовка...\n\n"
                    f"🎞 Цель: {target_height}p\n"
                    f"📦 Оценка: неизвестна"
                )
        else:
            use_worker = True
    except Exception:
        est_local = estimate_size_mb_local(text, DEFAULT_HEIGHT)
        if est_local is None:
            use_worker = True
            target_height = DEFAULT_HEIGHT
        else:
            target_height = LOWER_HEIGHT if est_local > WORKER_TRIGGER_MB else DEFAULT_HEIGHT
            use_worker = est_local > WORKER_TRIGGER_MB

    if not use_worker:
        try:
            await msg.edit_text(
                f"📥 Скачивание видео...\n\n"
                f"🎞 Цель: {target_height}p\n"
                f"⏳ 0%"
            )
            video_path = await local_download(text, target_height, msg)
        except Exception as e:
            use_worker = True
            try:
                await msg.edit_text(
                    f"⚠️ Локально не получилось, переключаюсь на сервер РФ...\n\n"
                    f"{str(e)[:800]}"
                )
            except Exception:
                pass

    if use_worker:
        video_path = await worker_download(text, target_height, msg)

    try:
        real_quality = get_real_resolution(str(video_path))
        size_mb = video_path.stat().st_size / 1024 / 1024

        await msg.edit_text(
            f"📤 Отправка видео...\n\n"
            f"🎞 {real_quality}\n"
            f"📦 {size_mb:.1f} MB"
        )

        caption = f"✅ Готово\n🎞 {real_quality}\n📦 {size_mb:.1f} MB"

        with open(video_path, "rb") as v:
            if size_mb <= LOCAL_MAX_MB:
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
                    read_timeout=3600,
                    write_timeout=3600,
                )
    finally:
        with contextlib.suppress(Exception):
            if "video_path" in locals() and video_path and Path(video_path).exists():
                Path(video_path).unlink(missing_ok=True)
        with contextlib.suppress(Exception):
            await msg.delete()


NORMAL_APP = Application.builder().token(TOKEN).build()
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
