import os
import json
TOKEN = os.environ.get("TOKEN")

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

# ──────────────────────────────────────────
# НАСТРОЙКИ БИЗНЕСА — меняй только здесь
BUSINESS_NAME = "Салон красоты"
SERVICES = [
    ["💇 Стрижка", "💅 Маникюр"],
    ["💆 Массаж", "✨ Другое"]
]
WELCOME_TEXT = "Добро пожаловать! Выберите услугу:"
# ──────────────────────────────────────────

CHOOSE_SERVICE, GET_NAME, GET_PHONE = range(3)
ADMIN_FILE = "admin.json"

def get_admin_id():
    """Читаем ID владельца из файла"""
    if os.path.exists(ADMIN_FILE):
        with open(ADMIN_FILE, "r") as f:
            data = json.load(f)
            return data.get("admin_id")
    return None

def save_admin_id(user_id):
    """Сохраняем ID владельца в файл"""
    with open(ADMIN_FILE, "w") as f:
        json.dump({"admin_id": user_id}, f)

# ──── КОМАНДЫ ВЛАДЕЛЬЦА ────

async def admin_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Владелец регистрируется командой /admin"""
    save_admin_id(update.message.from_user.id)
    await update.message.reply_text(
        "✅ Вы зарегистрированы как владелец.\n"
        "Теперь все заявки будут приходить вам.\n\n"
        f"Ваш ID: {update.message.from_user.id}"
    )

async def admin_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка — кто сейчас получает заявки"""
    admin_id = get_admin_id()
    if admin_id:
        await update.message.reply_text(f"📋 Заявки получает пользователь с ID: {admin_id}")
    else:
        await update.message.reply_text("⚠️ Владелец не настроен. Отправьте /admin")

# ──── ДИАЛОГ С КЛИЕНТОМ ────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие клиента"""
    admin_id = get_admin_id()
    if not admin_id:
        await update.message.reply_text(
            "⚠️ Бот ещё не настроен. Владелец скоро всё исправит!"
        )
        return ConversationHandler.END

    keyboard = ReplyKeyboardMarkup(SERVICES, resize_keyboard=True)
    await update.message.reply_text(
        f"👋 {WELCOME_TEXT}",
        reply_markup=keyboard
    )
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
    name    = context.user_data["name"]
    phone   = context.user_data["phone"]
    user    = update.message.from_user

    # Сообщение клиенту
    await update.message.reply_text(
        "✅ Заявка принята!\n\n"
        f"Услуга: {service}\n"
        f"Имя: {name}\n"
        f"Телефон: {phone}\n\n"
        "Мы свяжемся с вами в ближайшее время! 😊"
    )

    # Уведомление владельцу
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

# ──── ЗАПУСК ────

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_service)],
            GET_NAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            GET_PHONE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("admin", admin_setup))
    app.add_handler(CommandHandler("whogets", admin_check))

    print("Бот запущен ✅")
    app.run_polling()

if __name__ == "__main__":
    main()
