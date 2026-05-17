import os
import re
import tempfile
import shutil
import asyncio

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# TOKEN
# =========================
TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")

# =========================
# SETTINGS
# =========================
THREADS = "8"

progress_regex = re.compile(r"(\d{1,3}(?:\.\d+)?)%")


# =========================
# HELPERS
# =========================
def parse_progress(line):
    match = progress_regex.search(line)
    if match:
        return float(match.group(1))
    return None


def format_mb(size_bytes):
    return round(size_bytes / 1024 / 1024, 1)


async def run_command(cmd):
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    output = []

    while True:
        line = await process.stdout.readline()

        if not line:
            break

        text = line.decode("utf-8", errors="ignore").strip()
        output.append(text)

    await process.wait()

    return process.returncode, output


# =========================
# START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Отправь YouTube ссылку — я скачаю видео\n\n"
        "Поддерживаются:\n"
        "- YouTube видео\n"
        "- Shorts\n"
        "- Плейлисты (первое видео)"
    )


# =========================
# DOWNLOAD
# =========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "youtube" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка")
        return

    msg = await update.message.reply_text("⏳ Получаю информацию о видео...")

    tmpdir = tempfile.mkdtemp(prefix="yt_")

    try:
        # =========================
        # Получаем информацию о видео
        # =========================
        info_cmd = [
            "yt-dlp",
            "--print", "%(filesize_approx)s",
            "--print", "%(title)s",
            "--print", "%(duration)s",
            "-f", "bestvideo+bestaudio/best",
            url
        ]
        
        code, info_output = await run_command(info_cmd)
        
        filesize = 0
        title = "video"
        duration = 0
        
        lines = [line for line in info_output if line.strip()]
        for line in lines:
            if line.isdigit() and len(line) > 3 and filesize == 0:
                filesize = int(line)
            elif line and not line.isdigit() and ":" not in line and len(line) > 3 and len(line) < 100:
                title = re.sub(r'[\\/*?:"<>|]', "", line)[:50]
            elif ":" in line and len(line) < 20:
                try:
                    parts = line.split(":")
                    if len(parts) == 2:
                        duration = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                except:
                    pass

        size_mb = format_mb(filesize) if filesize else 0
        
        if size_mb == 0 and duration > 0:
            size_mb = round(duration / 60 * 10, 1)

        # =========================
        # Выбор качества с аудио
        # =========================
        if size_mb <= 100:
            quality = "1080p"
            # ИСПРАВЛЕНО: правильный формат для получения видео с аудио
            format_string = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best"
        elif size_mb <= 180:
            quality = "720p"
            format_string = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best"
        else:
            quality = "480p"
            format_string = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best"

        await msg.edit_text(
            f"⏳ Скачиваю {quality}...\n"
            f"📊 Размер: ~{size_mb} MB\n"
            f"📹 Название: {title}"
        )

        # =========================
        # Скачивание видео с аудио
        # =========================
        output_template = os.path.join(tmpdir, f"{title}.%(ext)s")

        cmd = [
            "yt-dlp",
            "-f", format_string,
            "-N", THREADS,
            "--merge-output-format", "mp4",
            "--embed-metadata",
            "-o", output_template,
            url
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        last_percent = -1

        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            text = line.decode("utf-8", errors="ignore").strip()
            percent = parse_progress(text)
            
            if percent is not None:
                current = int(percent)
                if current != last_percent and current % 10 == 0:
                    last_percent = current
                    try:
                        await msg.edit_text(
                            f"⏳ Скачиваю {quality}...\n"
                            f"📊 {current}%"
                        )
                    except:
                        pass

        await process.wait()

        # =========================
        # Поиск скачанного файла (улучшенный)
        # =========================
        await asyncio.sleep(2)  # Увеличил задержку
        
        downloaded_file = None
        all_files = []
        
        # Рекурсивный поиск всех файлов
        for root, dirs, files in os.walk(tmpdir):
            for file in files:
                file_path = os.path.join(root, file)
                file_size = os.path.getsize(file_path)
                all_files.append((file_path, file_size, file))
        
        # Сортируем по размеру (от большего к меньшему)
        all_files.sort(key=lambda x: x[1], reverse=True)
        
        # Ищем самый большой видеофайл
        for file_path, file_size, file_name in all_files:
            if file_size > 500000:  # больше 500KB
                # Проверяем расширение или наличие video в mime
                if file_name.lower().endswith(('.mp4', '.mkv', '.webm', '.mov', '.avi')):
                    downloaded_file = file_path
                    break
        
        # Если не нашли по расширению, берем самый большой файл
        if not downloaded_file and all_files:
            for file_path, file_size, file_name in all_files:
                if file_size > 1000000:  # больше 1MB
                    downloaded_file = file_path
                    break

        if not downloaded_file:
            debug_info = "\n".join([f"- {f[2]} ({format_mb(f[1])} MB)" for f in all_files[:5]])
            await msg.edit_text(
                f"❌ Видео не найдено\n\n"
                f"Найденные файлы:\n{debug_info}"
            )
            return

        final_size = format_mb(os.path.getsize(downloaded_file))

        # =========================
        # Проверка размера
        # =========================
        if final_size > 1900:
            await msg.edit_text(
                f"❌ Видео слишком большое\n"
                f"📦 {final_size} MB (макс. 1900 MB)"
            )
            return

        # =========================
        # Проверка наличия аудио (через ffprobe)
        # =========================
        has_audio = False
        try:
            probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "stream=codec_type",
                "-of", "default=noprint_wrappers=1",
                downloaded_file
            ]
            process = await asyncio.create_subprocess_exec(
                *probe_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            has_audio = "codec_type=audio" in stdout.decode()
        except:
            pass

        audio_status = "🔊 с аудио" if has_audio else "🔇 без аудио"

        # =========================
        # Отправка видео
        # =========================
        await msg.edit_text(
            f"📤 Отправляю видео...\n"
            f"🎬 {quality}\n"
            f"📦 {final_size} MB\n"
            f"{audio_status}"
        )

        with open(downloaded_file, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=f"✅ Готово!\n"
                       f"🎬 {quality}\n"
                       f"📦 {final_size} MB\n"
                       f"{audio_status}\n"
                       f"📹 {title[:40]}",
                supports_streaming=True,
                read_timeout=600,
                write_timeout=600,
            )

        await msg.delete()

    except Exception as e:
        error_text = str(e)
        print(f"Ошибка: {error_text}")
        await msg.edit_text(f"❌ Ошибка: {error_text[:200]}")
        
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except:
            pass


# =========================
# MAIN
# =========================
def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ Ошибка: Вставь токен бота")
        print("\nВариант 1 - переменная окружения:")
        print("  export BOT_TOKEN='твой_токен'")
        print("  python bot.py")
        print("\nВариант 2 - прямо в коде:")
        print("  TOKEN = 'твой_токен'")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Бот запущен...")
    print(f"📝 Потоков: {THREADS}")
    
    # Проверка наличия ffmpeg/ffprobe
    print("\n🔍 Проверка зависимостей:")
    
    import shutil as sh
    ffmpeg_path = sh.which("ffmpeg")
    ffprobe_path = sh.which("ffprobe")
    ytdlp_path = sh.which("yt-dlp")
    
    if ytdlp_path:
        print(f"  ✅ yt-dlp: {ytdlp_path}")
    else:
        print(f"  ❌ yt-dlp не найден! Установите: pip install yt-dlp")
    
    if ffmpeg_path:
        print(f"  ✅ ffmpeg: {ffmpeg_path}")
    else:
        print(f"  ❌ ffmpeg не найден! Установите:")
        print(f"     Ubuntu: sudo apt install ffmpeg")
        print(f"     Mac: brew install ffmpeg")
        print(f"     Windows: скачайте с ffmpeg.org")
    
    if ffprobe_path:
        print(f"  ✅ ffprobe: {ffprobe_path}")
    
    print("\n✅ Бот готов к работе!")
    
    app.run_polling()


if __name__ == "__main__":
    main()
