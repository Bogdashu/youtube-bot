import os, re, json, uuid, asyncio, tempfile, subprocess, base64, httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters)

TOKEN = os.getenv("BOT_TOKEN")
RF_WORKER_URL = os.getenv("RF_WORKER_URL")
WORKER_SECRET = os.getenv("WORKER_SECRET")
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL")

COOKIES_FILE = None
_cookies_b64 = os.getenv("YT_COOKIES_B64")
if _cookies_b64:
    COOKIES_FILE = "/tmp/cookies.txt"
    with open(COOKIES_FILE, "wb") as _cf:
        _cf.write(base64.b64decode(_cookies_b64))

COMMON = ["--js-runtimes", "node", "--no-playlist",
          "--socket-timeout", "30", "--retries", "5",
          "--extractor-args", "youtube:player_client=android_vr,web"]
if COOKIES_FILE:
    COMMON += ["--cookies", COOKIES_FILE]

TG_DIRECT_MB = 200
VIDEO_FACTOR = 0.5
PENDING = {}

COMMON = ["--js-runtimes", "node", "--no-playlist",
          "--socket-timeout", "30", "--retries", "5",
          "--extractor-args", "youtube:player_client=android_vr,web"]

def ydlp_info(url):
    p = subprocess.run(["yt-dlp", *COMMON, "-J", url],
                       capture_output=True, text=True)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "yt-dlp failed").strip()
        raise RuntimeError(err[-800:])   # покажем настоящую ошибку
    return json.loads(p.stdout)

def _fmt_size(f):
    # Точный filesize берём как есть; только filesize_approx уменьшаем.
    if not f:
        return 0
    if f.get("filesize"):
        return f["filesize"]
    if f.get("filesize_approx"):
        return int(f["filesize_approx"] * VIDEO_FACTOR)
    return 0

def _best(cands):
    if not cands:
        return None
    return max(cands, key=lambda f: ((f.get("height") or 0),
                                     (f.get("fps") or 0),
                                     (f.get("tbr") or 0)))

def pick_sizes(info):
    fmts = info.get("formats", [])
    vids = [f for f in fmts if f.get("vcodec") != "none" and f.get("acodec") == "none"]
    auds = [f for f in fmts if f.get("acodec") != "none" and f.get("vcodec") == "none"]

    a_m4a = [f for f in auds if f.get("ext") == "m4a"]
    a_size = _sz(_best(a_m4a) or _best(auds))   # аудио как есть (оно точное)

    def vid_total(maxh):
        pool = [f for f in vids if (f.get("height") or 0) <= maxh]
        if pool:
            mp4 = [f for f in pool if f.get("ext") == "mp4"]
            return _sz(_best(mp4) or _best(pool)) + a_size
        comb = [f for f in fmts if f.get("vcodec") != "none" and f.get("acodec") != "none"
                and (f.get("height") or 0) <= maxh]
        cmp4 = [f for f in comb if f.get("ext") == "mp4"]
        return _sz(_best(cmp4) or _best(comb))

    # фактор применяем к ИТОГУ по видео; аудио не трогаем
    return {"1080": int(vid_total(1080) * VIDEO_FACTOR),
            "720":  int(vid_total(720)  * VIDEO_FACTOR),
            "audio": a_size}

mb = lambda b: b / 1024 / 1024

def fmt_for(mode):
    if mode == "audio":
        return "bestaudio[ext=m4a]/bestaudio/best"
    h = 1080 if mode == "1080" else 720
    return (f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height<={h}]+ba/"
            f"b[height<={h}][ext=mp4]/b[height<={h}]/b")

def get_real_resolution(filepath):
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=height", "-of", "csv=p=0", filepath]
        out = subprocess.check_output(cmd, text=True).strip().splitlines()
        return f"{out[0]}p" if out and out[0] else "unknown"
    except Exception:
        return "unknown"

# ---- локальный Bot API: ленивая инициализация ----
LOCAL_APP = None
if LOCAL_BOT_API_URL:
    LOCAL_APP = (Application.builder().token(TOKEN)
                 .base_url(f"{LOCAL_BOT_API_URL}/bot")
                 .base_file_url(f"{LOCAL_BOT_API_URL}/file/bot")
                 .local_mode(True).build())

_local_ready = False
async def get_local_bot():
    global _local_ready
    if LOCAL_APP is None:
        return None
    if not _local_ready:
        try:
            await LOCAL_APP.initialize()
            _local_ready = True
        except Exception as e:
            print(f"[local API недоступен] {e}")
            return None
    return LOCAL_APP.bot

async def start(update, context):
    await update.message.reply_text("🎬 Отправь YouTube ссылку")

async def handle_message(update, context):
    url = update.message.text.strip()
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка"); return
    msg = await update.message.reply_text("🎞 Считаю размеры...")
    try:
        info = await asyncio.to_thread(ydlp_info, url)
        s = pick_sizes(info)
    except Exception as e:
        await msg.edit_text(f"❌ Не удалось получить инфо\n{e}"); return
    title = info.get("title", "видео")
    token = uuid.uuid4().hex[:12]
    PENDING[token] = {"url": url, "title": title, "sizes": s}
    lbl = lambda n, x: f"{n} • ~{mb(x):.0f} MB" if x else f"{n} • ?"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl("1080p", s["1080"]), callback_data=f"dl|{token}|1080")],
        [InlineKeyboardButton(lbl("720p",  s["720"]),  callback_data=f"dl|{token}|720")],
        [InlineKeyboardButton(lbl("🎵 Аудио", s["audio"]), callback_data=f"dl|{token}|audio")],
    ])
    await msg.edit_text(f"🎬 {title}\n\nВыбери качество:", reply_markup=kb)

async def run_progress(cmd, q, prefix):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    rx = re.compile(r"(\d{1,3}(?:\.\d+)?)%"); last = -5; tail = []
    async for raw in proc.stdout:
        line = raw.decode(errors="ignore"); tail.append(line); tail[:] = tail[-15:]
        m = rx.search(line)
        if m:
            p = float(m.group(1))
            if p - last >= 5:
                last = p
                try: await q.edit_message_text(f"{prefix}\n⏳ {p:.0f}%")
                except: pass
    await proc.wait()
    return proc.returncode, "".join(tail[-6:])

async def upload_to_rf(q, filepath, mode, title, size):
    """Заливает уже скачанный файл на РФ и отдаёт ссылку. РФ ничего не качает."""
    if not RF_WORKER_URL:
        await q.edit_message_text("❌ RF_WORKER_URL не настроен"); return
    await q.edit_message_text(f"📤 Заливаю на сервер... 📦 {size:.0f} MB")
    ext = os.path.splitext(filepath)[1] or ".mp4"
    headers = {"X-Secret": WORKER_SECRET}
    try:
        async with httpx.AsyncClient(timeout=None) as cl:
            with open(filepath, "rb") as fh:
                files = {"file": ("v" + ext, fh, "application/octet-stream")}
                data = {"title": title, "ext": ext}
                r = await cl.post(f"{RF_WORKER_URL}/upload",
                                  files=files, data=data, headers=headers)
                r.raise_for_status()
                resp = r.json()
    except Exception as e:
        await q.edit_message_text(f"❌ Не удалось залить файл\n{e}"); return
    job = resp["job_id"]; dl_token = resp["dl_token"]
    real_mb = resp.get("size_mb") or size
    file_url = f"{RF_WORKER_URL}/jobs/{job}/file?t={dl_token}"
    qlabel = "🎵 Аудио" if mode == "audio" else f"🎞 {mode}p"
    await q.edit_message_text(
        f"✅ Готово\n{title}\n{qlabel} • 📦 {real_mb:.1f} MB\n\n"
        f"📥 Скачать файл (нажми ссылку):\n{file_url}\n\n"
        f"⚠️ Ссылка активна ~10 минут.",
        disable_web_page_preview=True)

async def on_railway(q, url, mode, title):
    chat_id = q.message.chat_id
    prefix = "📥 Скачивание (аудио)..." if mode == "audio" else f"📥 Скачивание ({mode}p)..."
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "v.%(ext)s")
        cmd = ["yt-dlp", *COMMON, "-N", "4", "-f", fmt_for(mode), "--newline", "-o", out, url]
        cmd += ["-x", "--audio-format", "m4a"] if mode == "audio" else ["--merge-output-format", "mp4"]
        rc, err = await run_progress(cmd, q, prefix)
        if rc != 0:
            await q.edit_message_text(f"❌ Ошибка yt-dlp\n{err[:800]}"); return
        f = next((os.path.join(tmp, x) for x in os.listdir(tmp)), None)
        if not f:
            await q.edit_message_text("❌ Файл не найден"); return
        size = os.path.getsize(f) / 1024 / 1024

        # больше TG_DIRECT_MB — ссылкой (заливаем готовый файл на РФ, без повторного скачивания)
        if size > TG_DIRECT_MB:
            await upload_to_rf(q, f, mode, title, size); return

        if mode == "audio":
            quality = "🎵 Аудио"
        else:
            real = await asyncio.to_thread(get_real_resolution, f)
            quality = f"🎞 {real}"
        cap = f"{title}\n\n{quality} • 📦 {size:.1f} MB"

        if size <= 49:
            app_bot = NORMAL_APP.bot
        else:
            app_bot = await get_local_bot()
            if app_bot is None:
                await upload_to_rf(q, f, mode, title, size); return

        await q.edit_message_text(f"📤 Отправка...\n{quality} • 📦 {size:.1f} MB")
        try:
            with open(f, "rb") as fh:
                if mode == "audio":
                    await app_bot.send_audio(chat_id=chat_id, audio=fh, caption=cap,
                                             read_timeout=1200, write_timeout=1200)
                else:
                    await app_bot.send_video(chat_id=chat_id, video=fh, caption=cap,
                                             supports_streaming=True,
                                             read_timeout=1200, write_timeout=1200)
        except Exception as e:
            print(f"[send failed] {e}")
            await upload_to_rf(q, f, mode, title, size); return
        try: await q.message.delete()
        except: pass

async def on_choice(update, context):
    q = update.callback_query; await q.answer()
    _, token, mode = q.data.split("|")
    data = PENDING.get(token)
    if not data:
        await q.edit_message_text("⌛ Ссылка устарела, пришли заново"); return
    url, title = data["url"], data["title"]
    await q.edit_message_text(f"📥 Готовлю ({'аудио' if mode=='audio' else mode+'p'})...")
    await on_railway(q, url, mode, title)   # всегда качаем на Railway

NORMAL_APP = Application.builder().token(TOKEN).build()

def main():
    NORMAL_APP.add_handler(CommandHandler("start", start))
    NORMAL_APP.add_handler(CallbackQueryHandler(on_choice, pattern=r"^dl\|"))
    NORMAL_APP.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("BOT STARTED")
    NORMAL_APP.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
