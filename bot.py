import os
import re
import json
import threading
import asyncio
import requests
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters
)

TOKEN = os.environ.get("TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# ══════════════════════════════════════════════════
#  НАСТРОЙКИ БИЗНЕСА — меняй только этот блок
# ══════════════════════════════════════════════════
BUSINESS_NAME = "Салон красоты «Демо»"

MASTERS = ["Анна", "Мария", "Ольга"]

SERVICES = ["💇 Стрижка", "💅 Маникюр", "💆 Массаж", "✨ Другое"]

WORK_HOURS = ["10:00", "11:00", "12:00", "13:00",
              "14:00", "15:00", "16:00", "17:00", "18:00"]

DAYS_AHEAD = 7          # на сколько дней вперёд открыта запись
TIMEZONE_OFFSET = 5     # часовой пояс бизнеса относительно UTC (Надым = +5)
# ══════════════════════════════════════════════════

ADMIN_FILE = "admin.json"
BOOKINGS_FILE = "bookings.json"

CHOOSE_MASTER, CHOOSE_SERVICE, CHOOSE_DATE, CHOOSE_TIME, GET_NAME, GET_PHONE, CONFIRM = range(7)

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

PHONE_RE = re.compile(r"^(\+7|7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}$")

# ──── ХРАНИЛИЩЕ ────

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_admin_id():
    return load_json(ADMIN_FILE, {}).get("admin_id")

def save_admin_id(user_id):
    save_json(ADMIN_FILE, {"admin_id": user_id})

def get_bookings():
    return load_json(BOOKINGS_FILE, {})

def slot_key(master, date, time):
    return f"{master}|{date}|{time}"

def now_local():
    return datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)

def normalize_phone(raw):
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in ("7", "8"):
        return "+7" + digits[1:]
    return None

def free_slots(master, date_str):
    """Свободные окна мастера на дату (прошедшее время сегодня скрывается)"""
    bookings = get_bookings()
    now = now_local()
    today_str = now.strftime("%Y-%m-%d")
    result = []
    for t in WORK_HOURS:
        if slot_key(master, date_str, t) in bookings:
            continue
        if date_str == today_str:
            h, m = map(int, t.split(":"))
            if now.hour > h or (now.hour == h and now.minute >= m):
                continue
        result.append(t)
    return result

def date_buttons(master):
    """Кнопки дат — показываем только дни где есть свободные окна"""
    rows, row = [], []
    for i in range(DAYS_AHEAD):
        d = now_local() + timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        if not free_slots(master, ds):
            continue
        label = "Сегодня" if i == 0 else "Завтра" if i == 1 else f"{WEEKDAYS[d.weekday()]} {d.strftime('%d.%m')}"
        row.append(InlineKeyboardButton(label, callback_data=f"d:{ds}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return rows

def pretty_date(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{WEEKDAYS[d.weekday()]}, {d.strftime('%d.%m.%Y')}"

# ──── КОМАНДЫ ВЛАДЕЛЬЦА ────

async def admin_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_admin_id(update.message.from_user.id)
    await update.message.reply_text(
        "✅ Вы зарегистрированы как владелец.\n"
        "Все заявки будут приходить вам.\n\n"
        "Команды:\n"
        "/bookings — посмотреть все записи"
    )

async def admin_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != get_admin_id():
        return
    bookings = get_bookings()
    if not bookings:
        await update.message.reply_text("📭 Записей пока нет.")
        return
    lines = ["📅 ВСЕ ЗАПИСИ:\n"]
    for key in sorted(bookings, key=lambda k: (k.split("|")[1], k.split("|")[2])):
        master, date, time = key.split("|")
        b = bookings[key]
        lines.append(
            f"— {pretty_date(date)} {time}\n"
            f"   Мастер: {master} | {b['service']}\n"
            f"   {b['name']}, {b['phone']}\n"
        )
    await update.message.reply_text("\n".join(lines))

# ──── ДИАЛОГ ЗАПИСИ ────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not get_admin_id():
        await update.message.reply_text("⚠️ Бот ещё не настроен.")
        return ConversationHandler.END
    context.user_data.clear()
    kb = [[InlineKeyboardButton(f"👤 {m}", callback_data=f"m:{i}")] for i, m in enumerate(MASTERS)]
    await update.message.reply_text(
        f"👋 Добро пожаловать в {BUSINESS_NAME}!\n\n"
        "Выберите мастера:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSE_MASTER

async def choose_master(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["master"] = MASTERS[int(q.data.split(":")[1])]
    kb, row = [], []
    for i, s in enumerate(SERVICES):
        row.append(InlineKeyboardButton(s, callback_data=f"s:{i}"))
        if len(row) == 2:
            kb.append(row); row = []
    if row:
        kb.append(row)
    await q.edit_message_text(
        f"Мастер: 👤 {context.user_data['master']}\n\n"
        "Выберите услугу:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSE_SERVICE

async def choose_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["service"] = SERVICES[int(q.data.split(":")[1])]
    kb = date_buttons(context.user_data["master"])
    if not kb:
        await q.edit_message_text(
            "😔 К сожалению, свободных окон нет.\n"
            "Попробуйте позже или напишите /start для выбора другого мастера."
        )
        return ConversationHandler.END
    await q.edit_message_text(
        f"Мастер: 👤 {context.user_data['master']}\n"
        f"Услуга: {context.user_data['service']}\n\n"
        "📅 Выберите день:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSE_DATE

async def choose_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    date_str = q.data.split(":", 1)[1]
    context.user_data["date"] = date_str
    slots = free_slots(context.user_data["master"], date_str)
    kb, row = [], []
    for t in slots:
        row.append(InlineKeyboardButton(f"🕐 {t}", callback_data=f"t:{t}"))
        if len(row) == 3:
            kb.append(row); row = []
    if row:
        kb.append(row)
    await q.edit_message_text(
        f"📅 {pretty_date(date_str)}\n\n"
        "Выберите удобное время:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CHOOSE_TIME

async def choose_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["time"] = q.data.split(":", 1)[1]
    await q.edit_message_text(
        f"🕐 Время: {context.user_data['time']}\n\n"
        "Как вас зовут?"
    )
    return GET_NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Пожалуйста, введите настоящее имя (минимум 2 буквы):")
        return GET_NAME
    context.user_data["name"] = name
    await update.message.reply_text(
        f"Приятно познакомиться, {name}! 😊\n\n"
        "📱 Укажите номер телефона:\n"
        "Например: +7 900 123-45-67 или 89001234567"
    )
    return GET_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if not PHONE_RE.match(raw):
        await update.message.reply_text(
            "❌ Неверный формат номера.\n\n"
            "Введите номер в формате:\n"
            "+7 900 123-45-67\n"
            "или 89001234567"
        )
        return GET_PHONE
    context.user_data["phone"] = normalize_phone(raw)
    d = context.user_data
    kb = [[
        InlineKeyboardButton("✅ Подтвердить", callback_data="confirm"),
        InlineKeyboardButton("🔄 Заново", callback_data="restart"),
    ]]
    await update.message.reply_text(
        "📋 Проверьте данные записи:\n\n"
        f"👤 Мастер: {d['master']}\n"
        f"💈 Услуга: {d['service']}\n"
        f"📅 Дата: {pretty_date(d['date'])}\n"
        f"🕐 Время: {d['time']}\n"
        f"Имя: {d['name']}\n"
        f"Телефон: {d['phone']}",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "restart":
        await q.edit_message_text("🔄 Хорошо, начнём заново. Напишите /start")
        return ConversationHandler.END

    d = context.user_data
    key = slot_key(d["master"], d["date"], d["time"])
    bookings = get_bookings()

    if key in bookings:  # окно заняли пока клиент заполнял
        await q.edit_message_text(
            "😔 Увы, это время только что заняли.\n"
            "Напишите /start и выберите другое окно."
        )
        return ConversationHandler.END

    bookings[key] = {
        "service": d["service"], "name": d["name"], "phone": d["phone"],
        "username": q.from_user.username or "не указан",
    }
    save_json(BOOKINGS_FILE, bookings)

    await q.edit_message_text(
        "🎉 Вы записаны!\n\n"
        f"👤 Мастер: {d['master']}\n"
        f"💈 Услуга: {d['service']}\n"
        f"📅 {pretty_date(d['date'])} в {d['time']}\n\n"
        "Ждём вас! Если планы изменятся — просто напишите нам."
    )

    admin_id = get_admin_id()
    if admin_id:
        username = f"@{q.from_user.username}" if q.from_user.username else "не указан"
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                f"🔔 НОВАЯ ЗАПИСЬ — {BUSINESS_NAME}\n\n"
                f"👤 Мастер: {d['master']}\n"
                f"💈 Услуга: {d['service']}\n"
                f"📅 {pretty_date(d['date'])} в {d['time']}\n"
                f"Клиент: {d['name']}\n"
                f"Телефон: {d['phone']}\n"
                f"Telegram: {username}"
            )
        )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Запись отменена. Напишите /start чтобы начать заново.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ──── СБОРКА ────

ptb_app = ApplicationBuilder().token(TOKEN).build()

conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        CHOOSE_MASTER:  [CallbackQueryHandler(choose_master, pattern=r"^m:")],
        CHOOSE_SERVICE: [CallbackQueryHandler(choose_service, pattern=r"^s:")],
        CHOOSE_DATE:    [CallbackQueryHandler(choose_date, pattern=r"^d:")],
        CHOOSE_TIME:    [CallbackQueryHandler(choose_time, pattern=r"^t:")],
        GET_NAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
        GET_PHONE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
        CONFIRM:        [CallbackQueryHandler(confirm, pattern=r"^(confirm|restart)$")],
    },
    fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
)
ptb_app.add_handler(conv)
ptb_app.add_handler(CommandHandler("admin", admin_setup))
ptb_app.add_handler(CommandHandler("bookings", admin_bookings))

loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
asyncio.run_coroutine_threadsafe(ptb_app.initialize(), loop).result()
print("Приложение инициализировано ✅")

# ──── FLASK ────

flask_app = Flask(__name__)

@flask_app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, ptb_app.bot)
    asyncio.run_coroutine_threadsafe(ptb_app.process_update(update), loop)
    return "OK"

@flask_app.route("/")
def index():
    return "Бот работает ✅"

if __name__ == "__main__":
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/setWebhook",
        json={"url": f"{WEBHOOK_URL}/{TOKEN}"}
    )
    print(f"Webhook установлен: {r.json()}")
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
