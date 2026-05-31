import os, re, json, uuid, asyncio, tempfile, subprocess, httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters)

TOKEN = os.getenv("BOT_TOKEN")
RF_WORKER_URL = os.getenv("RF_WORKER_URL")        # http://RF_PUBLIC_IP:PORT
WORKER_SECRET = os.getenv("WORKER_SECRET")

TG_DIRECT_MB = 100        # ≤ этого Railway заливает сам; выше — ссылкой с РФ
PENDING = {}

COMMON = ["--js-runtimes","node","--no-playlist",
          "--extractor-args","youtube:player_client=android_vr,web"]

def ydlp_info(url):
    out = subprocess.check_output(["yt-dlp", *COMMON, "-J", url],
                                  text=True, stderr=subprocess.DEVNULL)
    return json.loads(out)

def pick_sizes(info):
    fmts = info.get("formats", [])
    sz = lambda f: f.get("filesize") or f.get("filesize_approx") or 0
    auds = [f for f in fmts if f.get("acodec")!="none" and f.get("vcodec")=="none"]
    a = sz(max(auds, key=sz)) if auds else 0
    def vid(maxh):
        v = [f for f in fmts if f.get("vcodec")!="none" and f.get("acodec")=="none"
             and (f.get("height") or 0)<=maxh]
        return sz(max(v, key=lambda f:((f.get("height") or 0), sz(f)))) if v else 0
    return {"1080": vid(1080)+a, "720": vid(720)+a, "audio": a}

mb = lambda b: b/1024/1024

def fmt_for(mode):
    if mode=="audio": return "bestaudio/best"
    h = 1080 if mode=="1080" else 720
    return f"(bv*[height<={h}]+ba/b)/best"

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
    token = uuid.uuid4().hex[:12]; PENDING[token] = url
    lbl = lambda n,x: f"{n} • {mb(x):.0f} MB" if x else f"{n} • ?"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl("1080p", s["1080"]), callback_data=f"dl|{token}|1080")],
        [InlineKeyboardButton(lbl("720p",  s["720"]),  callback_data=f"dl|{token}|720")],
        [InlineKeyboardButton(lbl("🎵 Аудио", s["audio"]), callback_data=f"dl|{token}|audio")],
    ])
    await msg.edit_text(f"🎬 {info.get('title','видео')}\nВыбери вариант:", reply_markup=kb)

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

async def on_railway(q, url, mode):
    """≤49 МБ и аудио: Railway качает и заливает напрямую."""
    chat_id = q.message.chat_id
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "v.%(ext)s")
        cmd = ["yt-dlp", *COMMON, "-N", "4", "-f", fmt_for(mode), "--newline", "-o", out, url]
        cmd += ["-x","--audio-format","m4a"] if mode=="audio" else ["--merge-output-format","mp4"]
        rc, err = await run_progress(cmd, q, f"📥 Качаю ({mode})...")
        if rc != 0:
            await q.edit_message_text(f"❌ Ошибка yt-dlp\n{err[:800]}"); return
        f = next((os.path.join(tmp, x) for x in os.listdir(tmp)), None)
        if not f: await q.edit_message_text("❌ Файл не найден"); return
        size = os.path.getsize(f)/1024/1024
        if size > TG_DIRECT_MB:
            # оказался больше ожидаемого — отдаём через РФ
            await q.edit_message_text("📥 Файл великоват, передаю на РФ-сервер...")
            await on_worker(q, url, mode, size); return
        await q.edit_message_text("📤 Отправка...")
        cap = f"✅ Готово • {size:.1f} MB"
        with open(f, "rb") as fh:
            if mode == "audio":
                await NORMAL_APP.bot.send_audio(chat_id=chat_id, audio=fh, caption=cap,
                                                read_timeout=600, write_timeout=600)
            else:
                await NORMAL_APP.bot.send_video(chat_id=chat_id, video=fh, caption=cap,
                                                supports_streaming=True,
                                                read_timeout=600, write_timeout=600)
    try: await q.message.delete()
    except: pass

async def on_worker(q, url, mode, size_mb):
    """>49 МБ: РФ качает, бот присылает прямую ссылку на файл."""
    if not RF_WORKER_URL:
        await q.edit_message_text("❌ RF_WORKER_URL не настроен"); return
    headers = {"X-Secret": WORKER_SECRET}
    async with httpx.AsyncClient(timeout=60) as cl:
        try:
            r = await cl.post(f"{RF_WORKER_URL}/jobs", json={"url": url, "mode": mode}, headers=headers)
            r.raise_for_status(); job = r.json()["job_id"]
        except Exception as e:
            await q.edit_message_text(f"❌ Не запустился РФ-воркер\n{e}"); return
        last = -5
        while True:
            await asyncio.sleep(3)
            try:
                st = (await cl.get(f"{RF_WORKER_URL}/jobs/{job}", headers=headers)).json()
            except Exception:
                continue
            if st["state"] == "error":
                await q.edit_message_text(f"❌ Ошибка воркера\n{st.get('error','')[:600]}"); return
            if st["state"] == "done": break
            p = st.get("percent", 0)
            if p - last >= 5:
                last = p
                try: await q.edit_message_text(f"📥 РФ-сервер качает ({mode})...\n⏳ {p:.0f}%")
                except: pass
    file_url = f"{RF_WORKER_URL}/jobs/{job}/file?secret={WORKER_SECRET}"
    await q.edit_message_text(
        f"✅ Готово • ~{size_mb:.0f} MB ({mode})\n\n"
        f"📥 Скачать файл (нажми ссылку):\n{file_url}\n\n"
        f"⚠️ Ссылка активна ~30 минут.",
        disable_web_page_preview=True)

async def on_choice(update, context):
    q = update.callback_query; await q.answer()
    _, token, mode = q.data.split("|")
    url = PENDING.get(token)
    if not url:
        await q.edit_message_text("⌛ Ссылка устарела, пришли заново"); return
    info = await asyncio.to_thread(ydlp_info, url)
    s = pick_sizes(info)
    size_mb = mb(s[mode])
    await q.edit_message_text(f"📥 Готовлю ({mode}, ~{size_mb:.0f} MB)...")
    if mode == "audio" or size_mb <= TG_DIRECT_MB:
        await on_railway(q, url, mode)
    else:
        await on_worker(q, url, mode, size_mb)

NORMAL_APP = Application.builder().token(TOKEN).build()

def main():
    NORMAL_APP.add_handler(CommandHandler("start", start))
    NORMAL_APP.add_handler(CallbackQueryHandler(on_choice, pattern=r"^dl\|"))
    NORMAL_APP.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("BOT STARTED")
    NORMAL_APP.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
