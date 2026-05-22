import os
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")

GATEWAY_URL = os.getenv("GATEWAY_URL")  # Румынский сервер
SECRET = os.getenv("SECRET")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отправь YouTube ссылку")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    msg = await update.message.reply_text("Отправляю задачу на сервер...")

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{GATEWAY_URL}/job",
            json={
                "url": url,
                "chat_id": update.effective_chat.id
            },
            headers={"X-Secret": SECRET}
        )

    if r.status_code != 200:
        await msg.edit_text("Ошибка отправки на сервер")
        return

    await msg.edit_text("Задача отправлена на сервер")

app = Application.builder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

app.run_polling()
