import os
import json
import asyncio
from flask import Flask, request
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

TOKEN = os.environ.get("TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# ──────────────────────────────────────────
BUSINESS_NAME = "Салон красоты"
SERVICES = [
    ["💇 Стрижка", "💅 Маникюр"],
    ["💆 Массаж", "✨ Другое"]
]
WELCOME_TEXT = "Добро пожаловать! Выберите услугу:"
ADMIN_FILE = "admin.json"
# ──────────────────────────────────────────

CHOOSE_SERVICE, GET_NAME, GET_PHONE = range(3)

def get_admin_id():
    if os.path.exists(ADMIN_FILE):
        with open(ADMIN_FILE, "r") as f:
            data = json.load(f)
            return data.get("admin_id")
    return None

def save_admin_id(user_id):
    with open(ADMIN_FILE, "w") as f:
        json.dump({"admin_id": user_id}, f)

async def admin_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_admin_id(update.message.from_user.id)
    await update.message.reply_text(
        f"✅ Вы зарегистрированы как владелец.\n"
        f"Ваш ID: {update.message.from_user.id}\n"
        f"Теперь все заявки будут приходить вам."
    )

async def admin_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = get_admin_id()
    if admin_id:
        await update.message.reply_text(f"📋 Заявки получает ID: {admin_id}")
    else:
        await update.message.reply_text("⚠️ Владелец не настроен. Отправьте /admin")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = get_admin_id()
    if not admin_id:
        await update.message.reply_text("⚠️ Бот ещё не настроен.")
        return ConversationHandler.END
    keyboard = ReplyKeyboardMarkup(SERVICES, resize_keyboard=True)
    await update.message.reply_text(f"👋 {WELCOME_TEXT}", reply_markup=keyboard)
    return CHOOSE_SERVICE

async def choose_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["service"] = update.message.text
    await update.message.reply_text(
        f"Отличный выбор: {update.message.text}\n\nКак вас зовут?",
        reply_markup=ReplyKeyboardRemove()
    )
    return GET_NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text
    await update.message.reply_text(
        f"Приятно познакомиться, {update.message.text}! 😊\n\nУкажите номер телефона:"
    )
    return GET_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text
    service = context.user_data["service"]
    name = context.user_data["name"]
    phone = context.user_data["phone"]
    user = update.message.from_user

    await update.message.reply_text(
        f"✅ Заявка принята!\n\n"
        f"Услуга: {service}\n"
        f"Имя: {name}\n"
        f"Телефон: {phone}\n\n"
        f"Мы свяжемся с вами в ближайшее время! 😊"
    )

    admin_id = get_admin_id()
    if admin_id:
        username = f"@{user.username}" if user.username else "не указан"
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"🔔 НОВАЯ ЗАЯВКА — {BUSINESS_NAME}\n\n"
                f"Услуга: {service}\n"
                f"Имя: {name}\n"
                f"Телефон: {phone}\n"
                f"Telegram: {username}"
            )
        )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Диалог отменён. Напишите /start чтобы начать заново.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ──── FLASK + WEBHOOK ────

flask_app = Flask(__name__)
ptb_app = ApplicationBuilder().token(TOKEN).build()

conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        CHOOSE_SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_service)],
        GET_NAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
        GET_PHONE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
    },
    fallbacks=[CommandHandler("cancel", cancel)]
)
ptb_app.add_handler(conv)
ptb_app.add_handler(CommandHandler("admin", admin_setup))
ptb_app.add_handler(CommandHandler("whogets", admin_check))

@flask_app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json()
    asyncio.run(
        ptb_app.process_update(Update.de_json(data, ptb_app.bot))
    )
    return "OK"

@flask_app.route("/")
def index():
    return "Бот работает ✅"

if __name__ == "__main__":
    import requests
    url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
    requests.post(url, json={"url": f"{WEBHOOK_URL}/{TOKEN}"})
    print("Webhook установлен ✅")
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
