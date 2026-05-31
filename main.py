import os, re, json, uuid, asyncio, tempfile, subprocess, httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters)

TOKEN = os.getenv("BOT_TOKEN")
RF_WORKER_URL = os.getenv("RF_WORKER_URL")
WORKER_SECRET = os.getenv("WORKER_SECRET")
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL")

TG_DIRECT_MB = 100
PENDING = {}

COMMON = ["--js-runtimes", "node", "--no-playlist",
          "--extractor-args", "youtube:player_client=android_vr,web"]

def ydlp_info(url):
    out = subprocess.check_output(["yt-dlp", *COMMON, "-J", url],
                                  text=True, stderr=subprocess.DEVNULL)
    return json.loads(out)

def pick_sizes(info):
    fmts = info.get("formats", [])
    sz = lambda f: f.get("filesize") or f.get("filesize_approx") or 0
    auds = [f for f in fmts if f.get("acodec") != "none" and f.get("vcodec") == "none"]
    a = sz(max(auds, key=sz)) if auds else 0
    def vid(maxh):
        v = [f for f in fmts if f.get("vcodec") != "none" and f.get("acodec") == "none"
             and (f.get("height") or 0) <= maxh]
        return sz(max(v, key=lambda f: ((f.get("height") or 0), sz(f)))) if v else 0
    return {"1080": vid(1080) + a, "720": vid(720) + a, "audio": a}

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

# ---- локальный Bot API: ленивая, безопасная инициализация ----
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
        if size > TG_DIRECT_MB:
            await q.edit_message_text("📥 Готовлю файл...")
            await on_worker(q, url, mode, title, size); return
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
                await q.edit_message_text("📥 Готовлю файл...")
                await on_worker(q, url, mode, title, size); return

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
            await q.edit_message_text("📥 Готовлю файл...")
            await on_worker(q, url, mode, title, size); return
        try: await q.message.delete()
        except: pass

async def on_worker(q, url, mode, title, size_mb):
    if not RF_WORKER_URL:
        await q.edit_message_text("❌ RF_WORKER_URL не настроен"); return
    headers = {"X-Secret": WORKER_SECRET}
    real_mb = size_mb
    async with httpx.AsyncClient(timeout=60) as cl:
        try:
            r = await cl.post(f"{RF_WORKER_URL}/jobs",
                              json={"url": url, "mode": mode, "title": title}, headers=headers)
            r.raise_for_status()
            resp = r.json()
            job = resp["job_id"]
            dl_token = resp["dl_token"]
        except Exception as e:
            await q.edit_message_text(f"❌ Не запустилась загрузка\n{e}"); return
        last = -5
        while True:
            await asyncio.sleep(3)
            try:
                st = (await cl.get(f"{RF_WORKER_URL}/jobs/{job}", headers=headers)).json()
            except Exception:
                continue
            if st["state"] == "error":
                await q.edit_message_text(f"❌ Ошибка загрузки\n{st.get('error','')[:600]}"); return
            if st["state"] == "done":
                real_mb = st.get("size_mb") or size_mb       # реальный размер файла
                break
            p = st.get("percent", 0)
            if p - last >= 5:
                last = p
                pref = "📥 Скачивание (аудио)..." if mode == "audio" else f"📥 Скачивание ({mode}p)..."
                try: await q.edit_message_text(f"{pref}\n⏳ {p:.0f}%")
                except: pass
    file_url = f"{RF_WORKER_URL}/jobs/{job}/file?t={dl_token}"
    qlabel = "🎵 Аудио" if mode == "audio" else f"🎞 {mode}p"
    await q.edit_message_text(
        f"✅ Готово\n{title}\n{qlabel} • 📦 {real_mb:.1f} MB\n\n"
        f"📥 Скачать файл (нажми ссылку):\n{file_url}\n\n"
        f"⚠️ Ссылка активна ~10 минут.",
        disable_web_page_preview=True)

async def on_choice(update, context):
    q = update.callback_query; await q.answer()
    _, token, mode = q.data.split("|")
    data = PENDING.get(token)
    if not data:
        await q.edit_message_text("⌛ Ссылка устарела, пришли заново"); return
    url, title, s = data["url"], data["title"], data["sizes"]
    size_mb = mb(s[mode])
    await q.edit_message_text(f"📥 Готовлю ({'аудио' if mode=='audio' else mode+'p'})...")
    if mode != "audio" and size_mb > TG_DIRECT_MB:
        await on_worker(q, url, mode, title, size_mb)
    else:
        await on_railway(q, url, mode, title)

NORMAL_APP = Application.builder().token(TOKEN).build()

def main():
    NORMAL_APP.add_handler(CommandHandler("start", start))
    NORMAL_APP.add_handler(CallbackQueryHandler(on_choice, pattern=r"^dl\|"))
    NORMAL_APP.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("BOT STARTED")
    NORMAL_APP.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
