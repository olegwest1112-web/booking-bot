import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = "8518866982:AAFIow5GsXT0oGnvvSJCb7T-wJ1EQdr-IR8"
ADMIN_ID = 8308164205
DB_PATH = "/app/booking.db"

logging.basicConfig(level=logging.INFO)
scheduler = AsyncIOScheduler()

WAITING_NAME = 1
WAITING_PHONE = 2
WAITING_ADMIN_DATE = 3
WAITING_ADMIN_TIME = 4
WAITING_ADMIN_CLOSE_DATE = 5
WAITING_ADMIN_CANCEL_ID = 6

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS work_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            is_open INTEGER DEFAULT 1
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS time_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            is_booked INTEGER DEFAULT 0,
            UNIQUE(date, time)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            time TEXT,
            name TEXT,
            phone TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_available_dates():
    conn = get_db()
    cur = conn.cursor()
    today = datetime.now().date()
    month_later = today + timedelta(days=30)
    cur.execute("""
        SELECT DISTINCT ts.date FROM time_slots ts
        JOIN work_days wd ON ts.date = wd.date
        WHERE wd.is_open = 1
        AND ts.is_booked = 0
        AND ts.date >= ? AND ts.date <= ?
        ORDER BY ts.date
    """, (str(today), str(month_later)))
    rows = cur.fetchall()
    conn.close()
    return [r["date"] for r in rows]

def get_available_times(date):
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now()
    cur.execute("""
        SELECT time FROM time_slots
        WHERE date = ? AND is_booked = 0
        ORDER BY time
    """, (date,))
    rows = cur.fetchall()
    conn.close()
    result = []
    for r in rows:
        slot_dt = datetime.strptime(f"{date} {r['time']}", "%Y-%m-%d %H:%M")
        if slot_dt > now:
            result.append(r["time"])
    return result

def get_user_booking(user_id):
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now()
    cur.execute("""
        SELECT * FROM bookings
        WHERE user_id = ? AND status = 'active'
        ORDER BY date DESC LIMIT 1
    """, (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        slot_dt = datetime.strptime(f"{row['date']} {row['time']}", "%Y-%m-%d %H:%M")
        if slot_dt < now:
            complete_old_bookings()
            return None
    return row

def complete_old_bookings():
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cur.execute("""
        UPDATE bookings SET status = 'completed'
        WHERE status = 'active' AND datetime(date || ' ' || time) < datetime(?)
    """, (now,))
    conn.commit()
    conn.close()

def create_booking(user_id, date, time, name, phone):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bookings (user_id, date, time, name, phone, status)
        VALUES (?, ?, ?, ?, ?, 'active')
    """, (user_id, date, time, name, phone))
    booking_id = cur.lastrowid
    cur.execute("UPDATE time_slots SET is_booked = 1 WHERE date = ? AND time = ?", (date, time))
    conn.commit()
    conn.close()
    return booking_id

def cancel_booking(booking_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT date, time FROM bookings WHERE id = ?", (booking_id,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
        cur.execute("UPDATE time_slots SET is_booked = 0 WHERE date = ? AND time = ?", (row["date"], row["time"]))
        conn.commit()
    conn.close()

def schedule_reminder(app, booking_id, user_id, date, time_str):
    remind_dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M") - timedelta(hours=24)
    if remind_dt <= datetime.now():
        return
    job_id = f"reminder_{booking_id}"
    scheduler.add_job(
        send_reminder,
        "date",
        run_date=remind_dt,
        args=[app, user_id, time_str],
        id=job_id,
        replace_existing=True
    )

async def send_reminder(app, user_id, time_str):
    try:
        await app.bot.send_message(
            chat_id=user_id,
            text=f"🔔 Нагадування!\n\nВи записані на завтра о {time_str}.\nЧекаємо на вас! ❤️"
        )
    except Exception as e:
        logging.error(f"Reminder error: {e}")

def main_menu():
    keyboard = [
        [KeyboardButton("📅 Записатись")],
        [KeyboardButton("📋 Мій запис")],
        [KeyboardButton("💅 Прайси"), KeyboardButton("🖼 Портфоліо")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def admin_menu():
    keyboard = [
        [KeyboardButton("➕ Додати день")],
        [KeyboardButton("➕ Додати слот")],
        [KeyboardButton("🔒 Закрити день"), KeyboardButton("🔓 Відкрити день")],
        [KeyboardButton("📅 Розклад на дату")],
        [KeyboardButton("📋 Майбутні записи")],
        [KeyboardButton("❌ Скасувати запис клієнта")],
        [KeyboardButton("◀️ Вийти з адмінки")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def cancel_menu():
    keyboard = [[KeyboardButton("❌ Скасувати")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def dates_keyboard(dates):
    keyboard = []
    for date in dates:
        dt = datetime.strptime(date, "%Y-%m-%d")
        label = dt.strftime("%d.%m.%Y (%a)").replace("Mon", "Пн").replace("Tue", "Вт").replace("Wed", "Ср").replace("Thu", "Чт").replace("Fri", "Пт").replace("Sat", "Сб").replace("Sun", "Нд")
        keyboard.append([InlineKeyboardButton(label, callback_data=f"date_{date}")])
    keyboard.append([InlineKeyboardButton("❌ Скасувати", callback_data="cancel_booking")])
    return InlineKeyboardMarkup(keyboard)

def times_keyboard(date, times):
    keyboard = []
    row = []
    for i, t in enumerate(times):
        row.append(InlineKeyboardButton(t, callback_data=f"time_{date}_{t}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_dates")])
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if uid == ADMIN_ID:
        context.user_data["admin_mode"] = False
    await update.message.reply_text(
        "👋 Привіт! Я допоможу записатись до майстра.\n\nОбери дію 👇",
        reply_markup=main_menu()
    )
    return ConversationHandler.END

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Немає доступу.")
        return
    context.user_data["admin_mode"] = True
    await update.message.reply_text("🔐 Адмін панель:", reply_markup=admin_menu())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.message.from_user.id

    # Адмін панель
    if context.user_data.get("admin_mode") and uid == ADMIN_ID:

        if text == "◀️ Вийти з адмінки":
            context.user_data.clear()
            await update.message.reply_text("Головне меню:", reply_markup=main_menu())
            return ConversationHandler.END

        elif text == "➕ Додати день":
            await update.message.reply_text(
                "Введи дату у форматі РРРР-ММ-ДД\nНаприклад: 2026-05-01",
                reply_markup=cancel_menu()
            )
            return WAITING_ADMIN_DATE

        elif text == "➕ Додати слот":
            await update.message.reply_text(
                "Введи дату і час у форматі РРРР-ММ-ДД ГГ:ХХ\nНаприклад: 2026-05-01 10:00",
                reply_markup=cancel_menu()
            )
            return WAITING_ADMIN_TIME

        elif text == "🔒 Закрити день":
            await update.message.reply_text(
                "Введи дату для закриття (РРРР-ММ-ДД):",
                reply_markup=cancel_menu()
            )
            return WAITING_ADMIN_CLOSE_DATE

        elif text == "🔓 Відкрити день":
            await update.message.reply_text(
                "Введи дату для відкриття (РРРР-ММ-ДД):",
                reply_markup=cancel_menu()
            )
            context.user_data["open_day"] = True
            return WAITING_ADMIN_CLOSE_DATE

        elif text == "📅 Розклад на дату":
            await update.message.reply_text(
                "Введи дату (РРРР-ММ-ДД):",
                reply_markup=cancel_menu()
            )
            context.user_data["view_schedule"] = True
            return WAITING_ADMIN_CLOSE_DATE

        elif text == "📋 Майбутні записи":
            conn = get_db()
            cur = conn.cursor()
            today = str(datetime.now().date())
            cur.execute("""
                SELECT b.id, b.date, b.time, b.name, b.phone, b.user_id
                FROM bookings b
                WHERE b.status = 'active' AND b.date >= ?
                ORDER BY b.date, b.time
            """, (today,))
            rows = cur.fetchall()
            conn.close()
            if rows:
                lines = []
                for r in rows:
                    lines.append(f"#{r['id']} | {r['date']} {r['time']} | {r['name']} | {r['phone']}")
                await update.message.reply_text(
                    "📋 Майбутні записи:\n\n" + "\n".join(lines),
                    reply_markup=admin_menu()
                )
            else:
                await update.message.reply_text("Записів немає.", reply_markup=admin_menu())

        elif text == "❌ Скасувати запис клієнта":
            await update.message.reply_text(
                "Введи ID запису (число):",
                reply_markup=cancel_menu()
            )
            return WAITING_ADMIN_CANCEL_ID

        return ConversationHandler.END

    # Звичайне меню
    if text == "📅 Записатись":
        existing = get_user_booking(uid)
        if existing:
            dt = datetime.strptime(f"{existing['date']} {existing['time']}", "%Y-%m-%d %H:%M")
            await update.message.reply_text(
                f"❌ У тебе вже є активний запис:\n\n"
                f"📅 {dt.strftime('%d.%m.%Y')} о {existing['time']}\n"
                f"👤 {existing['name']}\n\n"
                f"Спочатку скасуй його через '📋 Мій запис'.",
                reply_markup=main_menu()
            )
            return ConversationHandler.END

        dates = get_available_dates()
        if not dates:
            await update.message.reply_text("На жаль, вільних дат немає. Спробуй пізніше.", reply_markup=main_menu())
            return ConversationHandler.END

        await update.message.reply_text(
            "Вибери дату:",
            reply_markup=dates_keyboard(dates)
        )

    elif text == "📋 Мій запис":
        booking = get_user_booking(uid)
        if booking:
            dt = datetime.strptime(f"{booking['date']} {booking['time']}", "%Y-%m-%d %H:%M")
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Скасувати запис", callback_data=f"cancel_{booking['id']}")]
            ])
            await update.message.reply_text(
                f"📋 Твій запис:\n\n"
                f"📅 Дата: {dt.strftime('%d.%m.%Y')}\n"
                f"🕐 Час: {booking['time']}\n"
                f"👤 Ім'я: {booking['name']}\n"
                f"📞 Телефон: {booking['phone']}",
                reply_markup=keyboard
            )
        else:
            await update.message.reply_text("У тебе немає активних записів.", reply_markup=main_menu())

    elif text == "💅 Прайси":
        await update.message.reply_text(
            "<b>💅 Прайси:</b>\n\n"
            "Манікюр — 500₴\n"
            "Педикюр — 600₴\n"
            "Нарощування — 800₴\n"
            "Корекція — 400₴",
            parse_mode="HTML",
            reply_markup=main_menu()
        )

    elif text == "🖼 Портфоліо":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Дивитись портфоліо 🖼", url="https://ru.pinterest.com/crystalwithluv/_created/")]
        ])
        await update.message.reply_text(
            "Ось наше портфоліо 👇",
            reply_markup=keyboard
        )

    return ConversationHandler.END

async def booking_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id

    if data == "cancel_booking":
        await query.edit_message_text("Скасовано.")
        return ConversationHandler.END

    elif data == "back_to_dates":
        dates = get_available_dates()
        if dates:
            await query.edit_message_text("Вибери дату:", reply_markup=dates_keyboard(dates))
        else:
            await query.edit_message_text("Вільних дат немає.")

    elif data.startswith("date_"):
        date = data.replace("date_", "")
        context.user_data["booking_date"] = date
        times = get_available_times(date)
        if not times:
            await query.edit_message_text("На цю дату немає вільних слотів.", reply_markup=dates_keyboard(get_available_dates()))
            return
        dt = datetime.strptime(date, "%Y-%m-%d")
        await query.edit_message_text(
            f"📅 {dt.strftime('%d.%m.%Y')}\n\nВибери час:",
            reply_markup=times_keyboard(date, times)
        )

    elif data.startswith("time_"):
        parts = data.split("_")
        date = parts[1]
        time_str = parts[2]
        context.user_data["booking_date"] = date
        context.user_data["booking_time"] = time_str
        await query.edit_message_text(
            f"📅 {datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m.%Y')} о {time_str}\n\nВведи своє ім'я:"
        )
        return WAITING_NAME

    elif data.startswith("cancel_"):
        booking_id = int(data.replace("cancel_", ""))
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Так, скасувати", callback_data=f"confirm_cancel_{booking_id}")],
            [InlineKeyboardButton("◀️ Ні, залишити", callback_data="keep_booking")],
        ])
        await query.edit_message_text("Ти впевнений що хочеш скасувати запис?", reply_markup=keyboard)

    elif data == "keep_booking":
        await query.edit_message_text("Запис збережено ✅")

    elif data.startswith("confirm_cancel_"):
        booking_id = int(data.replace("confirm_cancel_", ""))
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM bookings WHERE id = ? AND user_id = ?", (booking_id, uid))
        booking = cur.fetchone()
        conn.close()

        if booking:
            cancel_booking(booking_id)
            job_id = f"reminder_{booking_id}"
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)

            await query.edit_message_text("✅ Запис скасовано.")

            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"❌ Клієнт скасував запис!\n\n"
                         f"#{booking_id} | {booking['date']} {booking['time']}\n"
                         f"👤 {booking['name']} | 📞 {booking['phone']}"
                )
            except:
                pass
        else:
            await query.edit_message_text("❌ Запис не знайдено.")

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        await update.message.reply_text("Скасовано.", reply_markup=main_menu())
        return ConversationHandler.END
    context.user_data["booking_name"] = text
    await update.message.reply_text("Введи номер телефону:", reply_markup=cancel_menu())
    return WAITING_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        await update.message.reply_text("Скасовано.", reply_markup=main_menu())
        return ConversationHandler.END

    uid = update.message.from_user.id
    date = context.user_data.get("booking_date")
    time_str = context.user_data.get("booking_time")
    name = context.user_data.get("booking_name")
    phone = text

    # Перевірка чи слот ще вільний
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT is_booked FROM time_slots WHERE date = ? AND time = ?", (date, time_str))
    slot = cur.fetchone()
    conn.close()

    if not slot or slot["is_booked"]:
        await update.message.reply_text(
            "❌ На жаль цей слот вже зайнятий. Спробуй вибрати інший час.",
            reply_markup=main_menu()
        )
        return ConversationHandler.END

    booking_id = create_booking(uid, date, time_str, name, phone)
    dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M")

    schedule_reminder(context.application, booking_id, uid, date, time_str)

    context.user_data.clear()

    await update.message.reply_text(
        f"✅ Запис підтверджено!\n\n"
        f"📅 Дата: {dt.strftime('%d.%m.%Y')}\n"
        f"🕐 Час: {time_str}\n"
        f"👤 Ім'я: {name}\n"
        f"📞 Телефон: {phone}\n\n"
        f"Чекаємо на тебе! ❤️",
        reply_markup=main_menu()
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🆕 Новий запис!\n\n"
                 f"#{booking_id} | {date} {time_str}\n"
                 f"👤 {name} | 📞 {phone}\n"
                 f"Telegram ID: {uid}"
        )
    except:
        pass

    return ConversationHandler.END

async def admin_add_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        await update.message.reply_text("Скасовано.", reply_markup=admin_menu())
        return ConversationHandler.END
    try:
        datetime.strptime(text, "%Y-%m-%d")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO work_days (date, is_open) VALUES (?, 1)", (text,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ День {text} додано!", reply_markup=admin_menu())
    except ValueError:
        await update.message.reply_text("❌ Невірний формат. Введи РРРР-ММ-ДД:", reply_markup=cancel_menu())
        return WAITING_ADMIN_DATE
    return ConversationHandler.END

async def admin_add_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        await update.message.reply_text("Скасовано.", reply_markup=admin_menu())
        return ConversationHandler.END
    try:
        parts = text.split(" ")
        date = parts[0]
        time_str = parts[1]
        datetime.strptime(date, "%Y-%m-%d")
        datetime.strptime(time_str, "%H:%M")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO work_days (date, is_open) VALUES (?, 1)", (date,))
        cur.execute("INSERT OR IGNORE INTO time_slots (date, time, is_booked) VALUES (?, ?, 0)", (date, time_str))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Слот {date} {time_str} додано!", reply_markup=admin_menu())
    except:
        await update.message.reply_text("❌ Невірний формат. Введи РРРР-ММ-ДД ГГ:ХХ:", reply_markup=cancel_menu())
        return WAITING_ADMIN_TIME
    return ConversationHandler.END

async def admin_close_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        context.user_data.pop("open_day", None)
        context.user_data.pop("view_schedule", None)
        await update.message.reply_text("Скасовано.", reply_markup=admin_menu())
        return ConversationHandler.END

    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("❌ Невірний формат. Введи РРРР-ММ-ДД:", reply_markup=cancel_menu())
        return WAITING_ADMIN_CLOSE_DATE

    if context.user_data.get("view_schedule"):
        context.user_data.pop("view_schedule", None)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT ts.time, ts.is_booked, b.name, b.phone, b.id
            FROM time_slots ts
            LEFT JOIN bookings b ON ts.date = b.date AND ts.time = b.time AND b.status = 'active'
            WHERE ts.date = ?
            ORDER BY ts.time
        """, (text,))
        rows = cur.fetchall()
        conn.close()
        if rows:
            lines = []
            for r in rows:
                if r["is_booked"]:
                    lines.append(f"🔴 {r['time']} — {r['name']} | {r['phone']} (#{r['id']})")
                else:
                    lines.append(f"🟢 {r['time']} — вільно")
            await update.message.reply_text(
                f"📅 Розклад на {text}:\n\n" + "\n".join(lines),
                reply_markup=admin_menu()
            )
        else:
            await update.message.reply_text("Слотів на цю дату немає.", reply_markup=admin_menu())

    elif context.user_data.get("open_day"):
        context.user_data.pop("open_day", None)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE work_days SET is_open = 1 WHERE date = ?", (text,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ День {text} відкрито!", reply_markup=admin_menu())

    else:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE work_days SET is_open = 0 WHERE date = ?", (text,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ День {text} закрито!", reply_markup=admin_menu())

    return ConversationHandler.END

async def admin_cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        await update.message.reply_text("Скасовано.", reply_markup=admin_menu())
        return ConversationHandler.END
    try:
        booking_id = int(text)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM bookings WHERE id = ? AND status = 'active'", (booking_id,))
        booking = cur.fetchone()
        conn.close()

        if not booking:
            await update.message.reply_text("❌ Запис не знайдено.", reply_markup=admin_menu())
            return ConversationHandler.END

        cancel_booking(booking_id)
        job_id = f"reminder_{booking_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        try:
            await context.bot.send_message(
                chat_id=booking["user_id"],
                text=f"❌ Ваш запис на {booking['date']} о {booking['time']} скасовано адміністратором.\n\nВибачте за незручності!"
            )
        except:
            pass

        await update.message.reply_text(f"✅ Запис #{booking_id} скасовано.", reply_markup=admin_menu())
    except ValueError:
        await update.message.reply_text("❌ Введи правильний ID.", reply_markup=cancel_menu())
        return WAITING_ADMIN_CANCEL_ID
    return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Скасовано.", reply_markup=main_menu())
    return ConversationHandler.END

def restore_reminders(app):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, user_id, date, time FROM bookings
            WHERE status = 'active'
        """)
        rows = cur.fetchall()
        conn.close()
        count = 0
        for r in rows:
            remind_dt = datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M") - timedelta(hours=24)
            if remind_dt > datetime.now():
                job_id = f"reminder_{r['id']}"
                scheduler.add_job(
                    send_reminder,
                    "date",
                    run_date=remind_dt,
                    args=[app, r["user_id"], r["time"]],
                    id=job_id,
                    replace_existing=True
                )
                count += 1
        print(f"Відновлено {count} нагадувань")
    except Exception as e:
        logging.error(f"Restore reminders error: {e}")

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
            CallbackQueryHandler(booking_callback),
        ],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            WAITING_ADMIN_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_day)],
            WAITING_ADMIN_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_slot)],
            WAITING_ADMIN_CLOSE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_close_day)],
            WAITING_ADMIN_CANCEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_cancel_booking)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel_command),
        ],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(conv)

    scheduler.start()
    restore_reminders(app)
    print("Бот запущено!")
    app.run_polling()

if __name__ == "__main__":
    main()
