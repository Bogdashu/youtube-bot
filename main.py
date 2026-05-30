import os, re, json, uuid, asyncio, tempfile, subprocess, logging, httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, filters)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ytbot")

TOKEN = os.getenv("BOT_TOKEN")
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL")
RF_WORKER_URL = (os.getenv("RF_WORKER_URL") or "").strip().rstrip("/")
if RF_WORKER_URL and not RF_WORKER_URL.startswith(("http://", "https://")):
    RF_WORKER_URL = "http://" + RF_WORKER_URL          # защита от голого IP
WORKER_SECRET = os.getenv("WORKER_SECRET")

LOCAL_LIMIT_MB = 120     # выше — на воркер РФ
TG_DIRECT_MB = 49        # выше — через локальный Bot API
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
        return "bestaudio/best"
    h = 1080 if mode == "1080" else 720
    return f"(bv*[height<={h}]+ba/b)/best"

async def start(update, context):
    await update.message.reply_text("🎬 Отправь YouTube ссылку")

async def handle_message(update, context):
    url = update.message.text.strip()
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Это не YouTube ссылка"); return
    msg = await update.message.reply_text("🎞 Считаю размеры…")
    try:
        info = await asyncio.to_thread(ydlp_info, url)
        s = pick_sizes(info)
    except Exception as e:
        await msg.edit_text(f"❌ Не удалось получить инфо\n{e}"); return
    token = uuid.uuid4().hex[:12]; PENDING[token] = url
    lbl = lambda n, x: f"{n} • {mb(x):.0f} MB" if x else f"{n} • ?"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl("1080p", s["1080"]), callback_data=f"dl|{token}|1080")],
        [InlineKeyboardButton(lbl("720p", s["720"]), callback_data=f"dl|{token}|720")],
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
                except Exception: pass
    await proc.wait()
    return proc.returncode, "".join(tail[-6:])

async def send_file(bot_normal, bot_local, chat_id, path, mode):
    size = os.path.getsize(path) / 1024 / 1024
    bot = bot_normal if size <= TG_DIRECT_MB else bot_local
    cap = f"✅ Готово • {size:.1f} MB"
    with open(path, "rb") as fh:
        if mode == "audio":
            await bot.send_audio(chat_id=chat_id, audio=fh, caption=cap,
                                 read_timeout=1800, write_timeout=1800,
                                 connect_timeout=60, pool_timeout=60)
        else:
            await bot.send_video(chat_id=chat_id, video=fh, caption=cap,
                                 supports_streaming=True,
                                 read_timeout=1800, write_timeout=1800,
                                 connect_timeout=60, pool_timeout=60)

async def on_railway(q, url, mode):
    chat_id = q.message.chat_id
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "v.%(ext)s")
        cmd = ["yt-dlp", *COMMON, "-N", "4", "-f", fmt_for(mode), "--newline", "-o", out, url]
        cmd += ["-x", "--audio-format", "m4a"] if mode == "audio" else ["--merge-output-format", "mp4"]
        rc, err = await run_progress(cmd, q, f"📥 Railway качает ({mode})…")
        if rc != 0:
            await q.edit_message_text(f"❌ Ошибка yt-dlp\n{err[:800]}"); return
        f = next((os.path.join(tmp, x) for x in os.listdir(tmp)), None)
        if not f:
            await q.edit_message_text("❌ Файл не найден"); return
        await q.edit_message_text("📤 Отправка…")
        try:
            await send_file(NORMAL_APP.bot, LOCAL_APP.bot, chat_id, f, mode)
        except Exception as e:
            log.exception("send_file failed")
            await q.edit_message_text(f"❌ Не удалось отправить:\n{type(e).__name__}: {str(e)[:500]}")
            return
    try: await q.message.delete()
    except Exception: pass

async def on_worker(q, url, mode, size_mb):
    chat_id = q.message.chat_id
    headers = {"X-Secret": WORKER_SECRET}
    # 1) ставим задачу воркеру и ждём готовности
    async with httpx.AsyncClient(timeout=60) as cl:
        r = await cl.post(f"{RF_WORKER_URL}/jobs", json={"url": url, "mode": mode}, headers=headers)
        r.raise_for_status(); job = r.json()["job_id"]
        last = -5
        while True:
            await asyncio.sleep(3)
            st = (await cl.get(f"{RF_WORKER_URL}/jobs/{job}", headers=headers)).json()
            if st["state"] == "error":
                await q.edit_message_text(f"❌ Ошибка воркера\n{st.get('error','')[:600]}"); return
            if st["state"] == "done": break
            p = st.get("percent", 0)
            if p - last >= 5:
                last = p
                try: await q.edit_message_text(f"📥 РФ-воркер качает ({mode})…\n⏳ {p:.0f}%")
                except Exception: pass

    # 2) заливаем в Telegram через локальный Bot API
    file_url = f"{RF_WORKER_URL}/jobs/{job}/file?secret={WORKER_SECRET}"
    cap = f"✅ Готово • ~{size_mb:.0f} MB"
    try: await q.edit_message_text(f"📤 Заливаю в Telegram (~{size_mb:.0f} MB)…")
    except Exception: pass
    try:
        if mode == "audio":
            await LOCAL_APP.bot.send_audio(chat_id=chat_id, audio=file_url, caption=cap,
                                           read_timeout=2400, write_timeout=2400,
                                           connect_timeout=60, pool_timeout=60)
        else:
            try:
                await LOCAL_APP.bot.send_video(chat_id=chat_id, video=file_url, caption=cap,
                                               supports_streaming=True,
                                               read_timeout=2400, write_timeout=2400,
                                               connect_timeout=60, pool_timeout=60)
            except Exception:
                log.exception("send_video failed, fallback to send_document")
                await LOCAL_APP.bot.send_document(chat_id=chat_id, document=file_url, caption=cap,
                                                  read_timeout=2400, write_timeout=2400,
                                                  connect_timeout=60, pool_timeout=60)
    except Exception as e:
        log.exception("upload to Telegram failed")
        try: await q.edit_message_text(f"❌ Не удалось залить:\n{type(e).__name__}: {str(e)[:500]}")
        except Exception: pass
        return

    # 3) уборка
    async with httpx.AsyncClient(timeout=30) as cl:
        try: await cl.delete(f"{RF_WORKER_URL}/jobs/{job}", headers=headers)
        except Exception: pass
    try: await q.message.delete()
    except Exception: pass

async def on_choice(update, context):
    q = update.callback_query; await q.answer()
    _, token, mode = q.data.split("|")
    url = PENDING.get(token)
    if not url:
        await q.edit_message_text("⌛ Ссылка устарела, пришли заново"); return
    info = await asyncio.to_thread(ydlp_info, url)
    s = pick_sizes(info)
    size_mb = mb(s[mode])
    await q.edit_message_text(f"📥 Готовлю ({mode}, ~{size_mb:.0f} MB)…")
    if mode == "audio" or size_mb <= LOCAL_LIMIT_MB:
        await on_railway(q, url, mode)
    else:
        await on_worker(q, url, mode, size_mb)

async def on_error(update, context):
    log.exception("handler error", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.callback_query:
            await update.callback_query.edit_message_text(
                f"❌ Ошибка: {type(context.error).__name__}: {str(context.error)[:400]}")
    except Exception:
        pass

# --- ВАЖНО: LOCAL_APP нужно инициализировать, иначе send_* через него падает ---
LOCAL_APP = (Application.builder().token(TOKEN)
             .base_url(f"{LOCAL_BOT_API_URL}/bot")
             .base_file_url(f"{LOCAL_BOT_API_URL}/file/bot")
             .local_mode(True).build())

async def _post_init(app):
    await LOCAL_APP.initialize()      # поднимаем HTTP-клиент локального бота
    log.info("LOCAL_APP initialized")

async def _post_shutdown(app):
    try: await LOCAL_APP.shutdown()
    except Exception: pass

NORMAL_APP = (Application.builder().token(TOKEN)
              .post_init(_post_init).post_shutdown(_post_shutdown).build())

def main():
    NORMAL_APP.add_handler(CommandHandler("start", start))
    NORMAL_APP.add_handler(CallbackQueryHandler(on_choice, pattern=r"^dl\|"))
    NORMAL_APP.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    NORMAL_APP.add_error_handler(on_error)
    print("BOT STARTED")
    NORMAL_APP.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
