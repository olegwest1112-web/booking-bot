import logging
import psycopg2
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = "8518866982:AAFIow5GsXT0oGnvvSJCb7T-wJ1EQdr-IR8"
ADMIN_ID = 8308164205
DATABASE_URL = "postgresql://postgres:wUgRMmkrWdpPmiEpaSvtNECydROvVUwC@postgres.railway.internal:5432/railway"

logging.basicConfig(level=logging.INFO)
scheduler = AsyncIOScheduler()

WAITING_NAME = 1
WAITING_PHONE = 2
WAITING_DESC = 3
WAITING_PHOTO = 4
WAITING_CONTACT = 5
WAITING_ADMIN_CANCEL_ID = 6
WAITING_ADMIN_PRICE = 7

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS work_days (
            id SERIAL PRIMARY KEY,
            date TEXT UNIQUE,
            is_open INTEGER DEFAULT 1
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS time_slots (
            id SERIAL PRIMARY KEY,
            date TEXT,
            time TEXT,
            is_booked INTEGER DEFAULT 0,
            UNIQUE(date, time)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            date TEXT,
            time TEXT,
            name TEXT,
            phone TEXT,
            description TEXT,
            contact TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS booking_photos (
            id SERIAL PRIMARY KEY,
            booking_id INTEGER,
            file_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS booking_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS waitlist (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            date TEXT,
            name TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            status TEXT DEFAULT 'waiting'
        )
    """)
    cur.execute("""
        INSERT INTO booking_settings (key, value)
        VALUES ('price_text', '💅 Прайс-лист

Манікюр:
- Класичний манікюр — 350₴
- Апаратний манікюр — 400₴
- Манікюр + гель-лак — 550₴
- Зняття гель-лаку — 100₴

Педикюр:
- Класичний педикюр — 500₴
- Апаратний педикюр — 550₴
- Педикюр + гель-лак — 700₴

Нарощування:
- Нарощування на форми — 900₴
- Нарощування на типси — 850₴
- Корекція нарощування — 600₴
- Зняття нарощування — 200₴

Дизайн:
- Стемпінг — від 50₴
- Втирка / кошачій eye — від 80₴
- Розпис (1 палець) — від 100₴
- Складний дизайн — від 200₴

💬 Точну ціну уточнюй у майстра')
        ON CONFLICT (key) DO NOTHING
    """)
    conn.commit()
    cur.close()
    conn.close()

def get_price_text():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM booking_settings WHERE key = 'price_text'")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else "Прайс не вказано"

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
        AND ts.date >= %s AND ts.date <= %s
        ORDER BY ts.date
    """, (str(today), str(month_later)))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r[0] for r in rows]

def get_all_dates_with_slots():
    conn = get_db()
    cur = conn.cursor()
    today = datetime.now().date()
    month_later = today + timedelta(days=30)
    cur.execute("""
        SELECT DISTINCT ts.date FROM time_slots ts
        JOIN work_days wd ON ts.date = wd.date
        WHERE wd.is_open = 1
        AND ts.date >= %s AND ts.date <= %s
        ORDER BY ts.date
    """, (str(today), str(month_later)))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r[0] for r in rows]

def get_available_times(date):
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now()
    cur.execute("SELECT time FROM time_slots WHERE date = %s AND is_booked = 0 ORDER BY time", (date,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = []
    for r in rows:
        slot_dt = datetime.strptime(date + " " + r[0], "%Y-%m-%d %H:%M")
        if slot_dt > now:
            result.append(r[0])
    return result

def get_user_booking(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, date, time, name, phone, description, contact, status FROM bookings WHERE user_id = %s AND status = 'active' ORDER BY date DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        slot_dt = datetime.strptime(row[2] + " " + row[3], "%Y-%m-%d %H:%M")
        if slot_dt < datetime.now():
            complete_old_bookings()
            return None
        return {"id": row[0], "user_id": row[1], "date": row[2], "time": row[3], "name": row[4], "phone": row[5], "description": row[6], "contact": row[7], "status": row[8]}
    return None

def complete_old_bookings():
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cur.execute("UPDATE bookings SET status = 'completed' WHERE status = 'active' AND (date || ' ' || time)::timestamp < %s::timestamp", (now,))
    conn.commit()
    cur.close()
    conn.close()

def create_booking(user_id, date, time, name, phone, description, contact, photos):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO bookings (user_id, date, time, name, phone, description, contact, status) VALUES (%s, %s, %s, %s, %s, %s, %s, 'active') RETURNING id", (user_id, date, time, name, phone, description, contact))
    booking_id = cur.fetchone()[0]
    cur.execute("UPDATE time_slots SET is_booked = 1 WHERE date = %s AND time = %s", (date, time))
    for photo_id in photos:
        cur.execute("INSERT INTO booking_photos (booking_id, file_id) VALUES (%s, %s)", (booking_id, photo_id))
    conn.commit()
    cur.close()
    conn.close()
    return booking_id

def cancel_booking(booking_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT date, time FROM bookings WHERE id = %s", (booking_id,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE bookings SET status = 'cancelled' WHERE id = %s", (booking_id,))
        cur.execute("UPDATE time_slots SET is_booked = 0 WHERE date = %s AND time = %s", (row[0], row[1]))
        conn.commit()
        cur.close()
        conn.close()
        return row[0]
    cur.close()
    conn.close()
    return None

def get_booking_by_id(booking_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, date, time, name, phone, description, contact, status FROM bookings WHERE id = %s", (booking_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {"id": row[0], "user_id": row[1], "date": row[2], "time": row[3], "name": row[4], "phone": row[5], "description": row[6], "contact": row[7], "status": row[8]}
    return None

def add_to_waitlist(user_id, date, name, phone):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM waitlist WHERE user_id = %s AND date = %s AND status = 'waiting'", (user_id, date))
    existing = cur.fetchone()
    if existing:
        cur.close()
        conn.close()
        return False
    cur.execute("INSERT INTO waitlist (user_id, date, name, phone, status) VALUES (%s, %s, %s, %s, 'waiting')", (user_id, date, name, phone))
    conn.commit()
    cur.close()
    conn.close()
    return True

def get_waitlist_for_date(date):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, name, phone FROM waitlist WHERE date = %s AND status = 'waiting' ORDER BY created_at", (date,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": r[0], "user_id": r[1], "name": r[2], "phone": r[3]} for r in rows]

def remove_from_waitlist(waitlist_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE waitlist SET status = 'notified' WHERE id = %s", (waitlist_id,))
    conn.commit()
    cur.close()
    conn.close()

def get_user_waitlist(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, date FROM waitlist WHERE user_id = %s AND status = 'waiting' ORDER BY created_at", (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": r[0], "date": r[1]} for r in rows]

def schedule_reminder(app, booking_id, user_id, date, time_str):
    remind_dt = datetime.strptime(date + " " + time_str, "%Y-%m-%d %H:%M") - timedelta(hours=24)
    if remind_dt <= datetime.now():
        return
    scheduler.add_job(send_reminder, "date", run_date=remind_dt, args=[app, user_id, time_str], id="reminder_" + str(booking_id), replace_existing=True)

async def send_reminder(app, user_id, time_str):
    try:
        await app.bot.send_message(chat_id=user_id, text="🔔 Нагадування!\n\nВи записані завтра о " + time_str + ".\nЧекаємо на вас! ❤️")
    except Exception as e:
        logging.error("Reminder error: " + str(e))

async def notify_waitlist(app, date, time_str):
    waitlist = get_waitlist_for_date(date)
    if not waitlist:
        return
    first = waitlist[0]
    remove_from_waitlist(first["id"])
    try:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Записатись", callback_data="waitlist_confirm_" + date + "_" + time_str + "_" + str(first["id"]))],
            [InlineKeyboardButton("❌ Відмовитись", callback_data="waitlist_decline_" + str(first["id"]))],
        ])
        await app.bot.send_message(
            chat_id=first["user_id"],
            text="🎉 З'явилось вільне місце!\n\n📅 " + datetime.strptime(date, "%Y-%m-%d").strftime('%d.%m.%Y') + " о " + time_str + "\n\n⏰ У тебе є 30 хвилин щоб підтвердити запис.\nПісля цього місце перейде до наступного в черзі.",
            reply_markup=keyboard
        )
        scheduler.add_job(
            expire_waitlist_offer,
            "date",
            run_date=datetime.now() + timedelta(minutes=30),
            args=[app, date, time_str, first["id"], first["user_id"]],
            id="waitlist_expire_" + str(first["id"]),
            replace_existing=True
        )
    except Exception as e:
        logging.error("Waitlist notify error: " + str(e))

async def expire_waitlist_offer(app, date, time_str, waitlist_id, user_id):
    try:
        await app.bot.send_message(
            chat_id=user_id,
            text="⏰ Час вийшов! Місце передано наступному в черзі."
        )
    except:
        pass
    await notify_waitlist(app, date, time_str)

def main_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📅 Записатись")],
        [KeyboardButton("📋 Мій запис"), KeyboardButton("🔔 Моя черга")],
        [KeyboardButton("💅 Прайси"), KeyboardButton("🖼 Портфоліо")],
    ], resize_keyboard=True)

def admin_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Додати день"), KeyboardButton("➕ Додати слот")],
        [KeyboardButton("🔒 Закрити день"), KeyboardButton("🔓 Відкрити день")],
        [KeyboardButton("📅 Розклад на дату")],
        [KeyboardButton("📋 Майбутні записи")],
        [KeyboardButton("❌ Скасувати запис клієнта")],
        [KeyboardButton("💰 Редагувати прайс")],
        [KeyboardButton("◀️ Вийти з адмінки")],
    ], resize_keyboard=True)

def cancel_menu():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Скасувати")]], resize_keyboard=True)

def skip_cancel_menu():
    return ReplyKeyboardMarkup([[KeyboardButton("➡️ Пропустити")], [KeyboardButton("❌ Скасувати")]], resize_keyboard=True)

def photo_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("✅ Готово — більше фото немає")],
        [KeyboardButton("➡️ Пропустити фото")],
        [KeyboardButton("❌ Скасувати")],
    ], resize_keyboard=True)

def dates_keyboard(dates):
    days_ua = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Нд"}
    keyboard = []
    for date in dates:
        dt = datetime.strptime(date, "%Y-%m-%d")
        day = days_ua.get(dt.strftime("%a"), dt.strftime("%a"))
        keyboard.append([InlineKeyboardButton(dt.strftime('%d.%m.%Y') + " (" + day + ")", callback_data="date_" + date)])
    keyboard.append([InlineKeyboardButton("❌ Скасувати", callback_data="cancel_booking")])
    return InlineKeyboardMarkup(keyboard)

def dates_with_waitlist_keyboard(dates):
    days_ua = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Нд"}
    keyboard = []
    for date in dates:
        dt = datetime.strptime(date, "%Y-%m-%d")
        day = days_ua.get(dt.strftime("%a"), dt.strftime("%a"))
        keyboard.append([InlineKeyboardButton("🔔 " + dt.strftime('%d.%m.%Y') + " (" + day + ")", callback_data="waitlist_" + date)])
    keyboard.append([InlineKeyboardButton("❌ Скасувати", callback_data="cancel_booking")])
    return InlineKeyboardMarkup(keyboard)

def times_keyboard(date, times):
    keyboard = []
    row = []
    for t in times:
        row.append(InlineKeyboardButton(t, callback_data="time_" + date + "_" + t))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад до дат", callback_data="back_to_dates")])
    return InlineKeyboardMarkup(keyboard)

def admin_dates_keyboard(action):
    days_ua = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Нд"}
    today = datetime.now().date()
    keyboard = []
    for i in range(10):
        dt = today + timedelta(days=i)
        day = days_ua.get(dt.strftime("%a"), dt.strftime("%a"))
        keyboard.append([InlineKeyboardButton(dt.strftime('%d.%m.%Y') + " (" + day + ")", callback_data="admin_" + action + "_" + dt.strftime('%Y-%m-%d'))])
    keyboard.append([InlineKeyboardButton("❌ Скасувати", callback_data="admin_cancel")])
    return InlineKeyboardMarkup(keyboard)

def admin_times_keyboard(date):
    keyboard = []
    row = []
    for h in range(9, 21):
        t = str(h).zfill(2) + ":00"
        row.append(InlineKeyboardButton(t, callback_data="admin_time_" + date + "_" + t))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("✏️ Ввести свій час", callback_data="admin_custom_time_" + date)])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_add_slot_back")])
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привіт! Я допоможу записатись до майстра манікюру.\n\nОбери дію 👇", reply_markup=main_menu())
    return ConversationHandler.END

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Немає доступу.")
        return
    context.user_data.clear()
    context.user_data["admin_mode"] = True
    await update.message.reply_text(
        "🔐 <b>Адмін панель</b>\n\n"
        "➕ <b>Додати день</b> — вибери дату з календаря\n\n"
        "➕ <b>Додати слот</b> — вибери дату і час\n\n"
        "🔒 <b>Закрити день</b> — заблокуй день\n\n"
        "🔓 <b>Відкрити день</b> — розблокуй день\n\n"
        "📅 <b>Розклад на дату</b> — переглянь слоти\n\n"
        "📋 <b>Майбутні записи</b> — всі активні записи\n\n"
        "❌ <b>Скасувати запис</b> — введи ID запису\n\n"
        "💰 <b>Редагувати прайс</b> — зміни прайс-лист\n\n"
        "Обери дію 👇",
        parse_mode="HTML",
        reply_markup=admin_menu()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.message.from_user.id

    if context.user_data.get("waiting_custom_time") and uid == ADMIN_ID:
        if text == "❌ Скасувати":
            context.user_data.pop("waiting_custom_time", None)
            await update.message.reply_text("Скасовано.", reply_markup=admin_menu())
            return ConversationHandler.END
        try:
            datetime.strptime(text, "%H:%M")
            date = context.user_data.get("admin_slot_date")
            conn = get_db()
            cur = conn.cursor()
            cur.execute("INSERT INTO work_days (date, is_open) VALUES (%s, 1) ON CONFLICT (date) DO NOTHING", (date,))
            cur.execute("INSERT INTO time_slots (date, time, is_booked) VALUES (%s, %s, 0) ON CONFLICT (date, time) DO NOTHING", (date, text))
            conn.commit()
            cur.close()
            conn.close()
            context.user_data.pop("waiting_custom_time", None)
            dt = datetime.strptime(date, "%Y-%m-%d")
            await update.message.reply_text("✅ Слот " + dt.strftime('%d.%m.%Y') + " о " + text + " додано!", reply_markup=admin_menu())
        except ValueError:
            await update.message.reply_text("❌ Невірний формат.\nВведи як <code>ГГ:ХХ</code>:", parse_mode="HTML", reply_markup=cancel_menu())
        return ConversationHandler.END

    if context.user_data.get("admin_mode") and uid == ADMIN_ID:

        if text == "◀️ Вийти з адмінки":
            context.user_data.clear()
            await update.message.reply_text("Головне меню:", reply_markup=main_menu())
            return ConversationHandler.END

        elif text == "➕ Додати день":
            await update.message.reply_text("📅 Вибери день для додавання:", reply_markup=admin_dates_keyboard("addday"))

        elif text == "➕ Додати слот":
            await update.message.reply_text("📅 Вибери дату для слота:", reply_markup=admin_dates_keyboard("addslot"))

        elif text == "🔒 Закрити день":
            await update.message.reply_text("🔒 Вибери день для закриття:", reply_markup=admin_dates_keyboard("closeday"))

        elif text == "🔓 Відкрити день":
            await update.message.reply_text("🔓 Вибери день для відкриття:", reply_markup=admin_dates_keyboard("openday"))

        elif text == "📅 Розклад на дату":
            await update.message.reply_text("📅 Вибери дату для перегляду:", reply_markup=admin_dates_keyboard("schedule"))

        elif text == "📋 Майбутні записи":
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id, date, time, name, phone FROM bookings WHERE status = 'active' AND date >= %s ORDER BY date, time", (str(datetime.now().date()),))
            rows = cur.fetchall()
            cur.close()
            conn.close()
            if rows:
                lines = []
                for r in rows:
                    dt = datetime.strptime(r[1] + " " + r[2], "%Y-%m-%d %H:%M").strftime('%d.%m %H:%M')
                    lines.append("#" + str(r[0]) + " | " + dt + " | " + r[3] + " | " + r[4])
                await update.message.reply_text("📋 <b>Майбутні записи:</b>\n\n" + "\n".join(lines) + "\n\n💡 ID використовуй для скасування", parse_mode="HTML", reply_markup=admin_menu())
            else:
                await update.message.reply_text("Активних записів немає.", reply_markup=admin_menu())

        elif text == "❌ Скасувати запис клієнта":
            await update.message.reply_text("❌ Введи ID запису (число).\nID видно в 📋 Майбутні записи", reply_markup=cancel_menu())
            return WAITING_ADMIN_CANCEL_ID

        elif text == "💰 Редагувати прайс":
            current = get_price_text()
            await update.message.reply_text("💰 <b>Поточний прайс:</b>\n\n" + current + "\n\nНадішли новий текст прайсу:", parse_mode="HTML", reply_markup=cancel_menu())
            return WAITING_ADMIN_PRICE

        return ConversationHandler.END

    if text == "📅 Записатись":
        existing = get_user_booking(uid)
        if existing:
            dt = datetime.strptime(existing['date'] + " " + existing['time'], "%Y-%m-%d %H:%M")
            await update.message.reply_text("❌ У тебе вже є активний запис:\n\n📅 " + dt.strftime('%d.%m.%Y') + " о " + existing['time'] + "\n👤 " + existing['name'] + "\n\nСпочатку скасуй його через '📋 Мій запис'.", reply_markup=main_menu())
            return ConversationHandler.END
        dates = get_available_dates()
        if not dates:
            all_dates = get_all_dates_with_slots()
            if all_dates:
                await update.message.reply_text(
                    "😔 На жаль всі слоти зайняті.\n\n🔔 Але ти можеш стати в чергу — якщо хтось скасує запис, ми одразу повідомимо тебе!\n\nВибери день в який хочеш потрапити:",
                    reply_markup=dates_with_waitlist_keyboard(all_dates)
                )
            else:
                await update.message.reply_text("На жаль, вільних дат немає 😔\nСпробуй пізніше.", reply_markup=main_menu())
            return ConversationHandler.END
        await update.message.reply_text("📅 Вибери зручну дату:", reply_markup=dates_keyboard(dates))

    elif text == "📋 Мій запис":
        booking = get_user_booking(uid)
        if booking:
            dt = datetime.strptime(booking['date'] + " " + booking['time'], "%Y-%m-%d %H:%M")
            desc = booking['description'] or "—"
            contact = booking['contact'] or "—"
            await update.message.reply_text(
                "📋 <b>Твій запис:</b>\n\n📅 " + dt.strftime('%d.%m.%Y') + "\n🕐 " + booking['time'] + "\n👤 " + booking['name'] + "\n📞 " + booking['phone'] + "\n📝 " + desc + "\n💬 " + contact,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Скасувати запис", callback_data="cancel_" + str(booking['id']))]])
            )
        else:
            await update.message.reply_text("У тебе немає активних записів.\n\nЗапишись через '📅 Записатись' 👇", reply_markup=main_menu())

    elif text == "🔔 Моя черга":
        waitlist = get_user_waitlist(uid)
        if waitlist:
            lines = []
            for w in waitlist:
                dt = datetime.strptime(w['date'], "%Y-%m-%d")
                lines.append("📅 " + dt.strftime('%d.%m.%Y'))
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Вийти з усіх черг", callback_data="leave_all_waitlist")]])
            await update.message.reply_text("🔔 <b>Ти в черзі на:</b>\n\n" + "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)
        else:
            await update.message.reply_text("Ти не стоїш в жодній черзі.", reply_markup=main_menu())

    elif text == "💅 Прайси":
        await update.message.reply_text(get_price_text(), reply_markup=main_menu())

    elif text == "🖼 Портфоліо":
        await update.message.reply_text(
            "✨ Наші роботи — дивись в Instagram 👇",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Instagram 📸", url="https://www.instagram.com/barabika.nails?igsh=MXFhZGRyY2d1M2M0cg==")]])
        )

    return ConversationHandler.END

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id

    if uid != ADMIN_ID:
        return

    if data == "admin_cancel":
        await query.edit_message_text("Скасовано.")

    elif data == "admin_add_slot_back":
        await query.edit_message_text("📅 Вибери дату для слота:", reply_markup=admin_dates_keyboard("addslot"))

    elif data.startswith("admin_addday_"):
        date = data.replace("admin_addday_", "")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO work_days (date, is_open) VALUES (%s, 1) ON CONFLICT (date) DO NOTHING", (date,))
        conn.commit()
        cur.close()
        conn.close()
        await query.edit_message_text("✅ День " + datetime.strptime(date, "%Y-%m-%d").strftime('%d.%m.%Y') + " додано!")

    elif data.startswith("admin_addslot_"):
        date = data.replace("admin_addslot_", "")
        context.user_data["admin_slot_date"] = date
        dt = datetime.strptime(date, "%Y-%m-%d")
        await query.edit_message_text("📅 Дата: " + dt.strftime('%d.%m.%Y') + "\n\n🕐 Вибери час:", reply_markup=admin_times_keyboard(date))

    elif data.startswith("admin_time_"):
        parts = data.replace("admin_time_", "").split("_")
        date = parts[0]
        time_str = parts[1]
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO work_days (date, is_open) VALUES (%s, 1) ON CONFLICT (date) DO NOTHING", (date,))
        cur.execute("INSERT INTO time_slots (date, time, is_booked) VALUES (%s, %s, 0) ON CONFLICT (date, time) DO NOTHING", (date, time_str))
        conn.commit()
        cur.close()
        conn.close()
        await query.edit_message_text("✅ Слот " + datetime.strptime(date, "%Y-%m-%d").strftime('%d.%m.%Y') + " о " + time_str + " додано!")

    elif data.startswith("admin_custom_time_"):
        date = data.replace("admin_custom_time_", "")
        context.user_data["admin_slot_date"] = date
        context.user_data["waiting_custom_time"] = True
        dt = datetime.strptime(date, "%Y-%m-%d")
        await query.edit_message_text(
            "📅 Дата: " + dt.strftime('%d.%m.%Y') + "\n\n✏️ Введи свій час у форматі <code>ГГ:ХХ</code>\nНаприклад: <code>10:30</code>\n\nАбо напиши ❌ Скасувати",
            parse_mode="HTML"
        )

    elif data.startswith("admin_closeday_"):
        date = data.replace("admin_closeday_", "")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE work_days SET is_open = 0 WHERE date = %s", (date,))
        conn.commit()
        cur.close()
        conn.close()
        await query.edit_message_text("✅ День " + datetime.strptime(date, "%Y-%m-%d").strftime('%d.%m.%Y') + " закрито!")

    elif data.startswith("admin_openday_"):
        date = data.replace("admin_openday_", "")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE work_days SET is_open = 1 WHERE date = %s", (date,))
        conn.commit()
        cur.close()
        conn.close()
        await query.edit_message_text("✅ День " + datetime.strptime(date, "%Y-%m-%d").strftime('%d.%m.%Y') + " відкрито!")

    elif data.startswith("admin_schedule_"):
        date = data.replace("admin_schedule_", "")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT ts.time, ts.is_booked, b.name, b.phone, b.id
            FROM time_slots ts
            LEFT JOIN bookings b ON ts.date = b.date AND ts.time = b.time AND b.status = 'active'
            WHERE ts.date = %s ORDER BY ts.time
        """, (date,))
        rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM waitlist WHERE date = %s AND status = 'waiting'", (date,))
        waitlist_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        dt = datetime.strptime(date, "%Y-%m-%d")
        if rows:
            lines = []
            for r in rows:
                if r[1]:
                    lines.append("🔴 " + r[0] + " — " + str(r[2]) + " | " + str(r[3]) + " (#" + str(r[4]) + ")")
                else:
                    lines.append("🟢 " + r[0] + " — вільно")
            text = "📅 <b>Розклад на " + dt.strftime('%d.%m.%Y') + ":</b>\n\n" + "\n".join(lines)
            if waitlist_count > 0:
                text += "\n\n🔔 В черзі: " + str(waitlist_count) + " чол."
            await query.edit_message_text(text, parse_mode="HTML")
        else:
            await query.edit_message_text("Слотів на " + dt.strftime('%d.%m.%Y') + " немає.")

async def booking_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id

    if data == "cancel_booking":
        await query.edit_message_text("Скасовано. Повертайся коли зручно! 😊")
        return ConversationHandler.END

    elif data == "back_to_dates":
        dates = get_available_dates()
        if dates:
            await query.edit_message_text("📅 Вибери зручну дату:", reply_markup=dates_keyboard(dates))
        else:
            await query.edit_message_text("Вільних дат немає 😔")

    elif data.startswith("waitlist_confirm_"):
        parts = data.replace("waitlist_confirm_", "").split("_")
        date = parts[0]
        time_str = parts[1]
        waitlist_id = parts[2]

        job_id = "waitlist_expire_" + waitlist_id
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT is_booked FROM time_slots WHERE date = %s AND time = %s", (date, time_str))
        slot = cur.fetchone()
        cur.close()
        conn.close()

        if not slot or slot[0]:
            await query.edit_message_text("❌ На жаль цей слот вже зайнятий.\n\nМи повідомимо тебе якщо з'явиться інший! 😔")
            await notify_waitlist(context.application, date, time_str)
            return

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT name, phone FROM waitlist WHERE id = %s", (int(waitlist_id),))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row:
            context.user_data["booking_date"] = date
            context.user_data["booking_time"] = time_str
            context.user_data["booking_name"] = row[0]
            context.user_data["booking_phone"] = row[1]
            context.user_data["booking_desc"] = "з черги очікування"
            context.user_data["booking_photos"] = []
            context.user_data["booking_contact"] = None

            booking_id = create_booking(uid, date, time_str, row[0], row[1], "з черги очікування", None, [])
            dt = datetime.strptime(date + " " + time_str, "%Y-%m-%d %H:%M")
            schedule_reminder(context.application, booking_id, uid, date, time_str)
            context.user_data.clear()

            await query.edit_message_text(
                "✅ <b>Запис підтверджено!</b>\n\n📅 " + dt.strftime('%d.%m.%Y') + "\n🕐 " + time_str + "\n\nЧекаємо на тебе! ❤️",
                parse_mode="HTML"
            )

            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="🆕 <b>Новий запис з черги!</b>\n\n#" + str(booking_id) + " | " + dt.strftime('%d.%m.%Y') + " о " + time_str + "\n👤 " + row[0] + "\n📞 " + row[1],
                    parse_mode="HTML"
                )
            except:
                pass

    elif data.startswith("waitlist_decline_"):
        waitlist_id = data.replace("waitlist_decline_", "")
        job_id = "waitlist_expire_" + waitlist_id
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        await query.edit_message_text("Зрозуміло! Якщо передумаєш — запишись знову 😊")

    elif data == "leave_all_waitlist":
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE waitlist SET status = 'cancelled' WHERE user_id = %s AND status = 'waiting'", (uid,))
        conn.commit()
        cur.close()
        conn.close()
        await query.edit_message_text("✅ Ти вийшов з усіх черг.")

    elif data.startswith("waitlist_"):
        date = data.replace("waitlist_", "")
        existing = get_user_booking(uid)
        if existing:
            await query.edit_message_text("❌ У тебе вже є активний запис. Спочатку скасуй його.")
            return

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT name, phone FROM bookings WHERE user_id = %s ORDER BY created_at DESC LIMIT 1", (uid,))
        last = cur.fetchone()
        cur.close()
        conn.close()

        name = last[0] if last else "Клієнт"
        phone = last[1] if last else "—"

        added = add_to_waitlist(uid, date, name, phone)
        dt = datetime.strptime(date, "%Y-%m-%d")

        if added:
            await query.edit_message_text(
                "✅ Тебе додано в чергу на " + dt.strftime('%d.%m.%Y') + "!\n\n🔔 Як тільки з'явиться вільний час — ми одразу напишемо тобі.\nУ тебе буде 30 хвилин щоб підтвердити запис."
            )
        else:
            await query.edit_message_text("Ти вже в черзі на цей день! 😊")

    elif data.startswith("date_"):
        date = data.replace("date_", "")
        context.user_data["booking_date"] = date
        times = get_available_times(date)
        if not times:
            all_dates = get_all_dates_with_slots()
            await query.edit_message_text(
                "😔 На цю дату всі слоти зайняті.\n\n🔔 Стань в чергу — якщо хтось скасує запис, ми одразу повідомимо тебе!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔔 Стати в чергу на цей день", callback_data="waitlist_" + date)],
                    [InlineKeyboardButton("◀️ Вибрати інший день", callback_data="back_to_dates")],
                ])
            )
            return
        dt = datetime.strptime(date, "%Y-%m-%d")
        days_ua = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Нд"}
        day = days_ua.get(dt.strftime("%a"), dt.strftime("%a"))
        await query.edit_message_text("📅 " + dt.strftime('%d.%m.%Y') + " (" + day + ")\n\n🕐 Вибери зручний час:", reply_markup=times_keyboard(date, times))

    elif data.startswith("time_"):
        parts = data.split("_")
        date = parts[1]
        time_str = parts[2]
        context.user_data["booking_date"] = date
        context.user_data["booking_time"] = time_str
        await query.edit_message_text("📅 " + datetime.strptime(date, "%Y-%m-%d").strftime('%d.%m.%Y') + " о " + time_str + "\n\n👤 Введи своє ім'я:")
        return WAITING_NAME

    elif data.startswith("cancel_"):
        booking_id = int(data.replace("cancel_", ""))
        await query.edit_message_text(
            "⚠️ Ти впевнений що хочеш скасувати запис?\n\nЦю дію не можна відмінити.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Так, скасувати", callback_data="confirm_cancel_" + str(booking_id))],
                [InlineKeyboardButton("◀️ Ні, залишити", callback_data="keep_booking")],
            ])
        )

    elif data == "keep_booking":
        await query.edit_message_text("✅ Запис збережено! Чекаємо на тебе ❤️")

    elif data.startswith("confirm_cancel_"):
        booking_id = int(data.replace("confirm_cancel_", ""))
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, date, time, name, phone, description, contact FROM bookings WHERE id = %s AND user_id = %s", (booking_id, uid))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row:
            booking = {"id": row[0], "user_id": row[1], "date": row[2], "time": row[3], "name": row[4], "phone": row[5], "description": row[6], "contact": row[7]}
            freed_date = cancel_booking(booking_id)
            job_id = "reminder_" + str(booking_id)
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
            await query.edit_message_text("✅ Запис скасовано.\n\nБудемо раді бачити тебе знову! 😊")

            times = get_available_times(booking['date'])
            if times and freed_date:
                await notify_waitlist(context.application, freed_date, booking['time'])

            try:
                dt = datetime.strptime(booking['date'] + " " + booking['time'], "%Y-%m-%d %H:%M")
                desc = booking['description'] or "—"
                contact = booking['contact'] or "—"
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="❌ <b>Клієнт скасував запис!</b>\n\n#" + str(booking['id']) + " | " + dt.strftime('%d.%m.%Y') + " о " + booking['time'] + "\n👤 " + booking['name'] + "\n📞 " + booking['phone'] + "\n📝 " + desc + "\n💬 " + contact,
                    parse_mode="HTML"
                )
            except Exception as e:
                logging.error("Admin notify error: " + str(e))
        else:
            await query.edit_message_text("❌ Запис не знайдено.")

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        await update.message.reply_text("Скасовано.", reply_markup=main_menu())
        return ConversationHandler.END
    context.user_data["booking_name"] = text
    await update.message.reply_text("👤 Ім'я: " + text + "\n\n📞 Введи номер телефону:", reply_markup=cancel_menu())
    return WAITING_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        await update.message.reply_text("Скасовано.", reply_markup=main_menu())
        return ConversationHandler.END
    context.user_data["booking_phone"] = text
    await update.message.reply_text("📞 Телефон: " + text + "\n\n📝 Опиши що хочеш зробити:\nНаприклад: манікюр з гель-лаком, корекція, дизайн метелики", reply_markup=cancel_menu())
    return WAITING_DESC

async def get_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        await update.message.reply_text("Скасовано.", reply_markup=main_menu())
        return ConversationHandler.END
    context.user_data["booking_desc"] = text
    context.user_data["booking_photos"] = []
    await update.message.reply_text(
        "📸 Надішли фото прикладу роботи (можна кілька).\n\nКоли надішлеш всі — натисни '✅ Готово'\nАбо '➡️ Пропустити фото' якщо фото немає",
        reply_markup=photo_menu()
    )
    return WAITING_PHOTO

async def get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text:
        text = update.message.text.strip()
        if text == "❌ Скасувати":
            await update.message.reply_text("Скасовано.", reply_markup=main_menu())
            return ConversationHandler.END
        elif text in ["✅ Готово — більше фото немає", "➡️ Пропустити фото"]:
            await update.message.reply_text(
                "💬 Залиш свій Instagram або Telegram для зв'язку з майстром.\nНаприклад: @username\n\nАбо натисни '➡️ Пропустити'",
                reply_markup=skip_cancel_menu()
            )
            return WAITING_CONTACT

    if update.message.photo:
        photos = context.user_data.get("booking_photos", [])
        photos.append(update.message.photo[-1].file_id)
        context.user_data["booking_photos"] = photos
        await update.message.reply_text("✅ Фото " + str(len(photos)) + " додано!\n\nДодай ще або натисни '✅ Готово'", reply_markup=photo_menu())
        return WAITING_PHOTO

    return WAITING_PHOTO

async def get_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        await update.message.reply_text("Скасовано.", reply_markup=main_menu())
        return ConversationHandler.END

    contact = None if text == "➡️ Пропустити" else text
    uid = update.message.from_user.id
    date = context.user_data.get("booking_date")
    time_str = context.user_data.get("booking_time")
    name = context.user_data.get("booking_name")
    phone = context.user_data.get("booking_phone")
    desc = context.user_data.get("booking_desc")
    photos = context.user_data.get("booking_photos", [])

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT is_booked FROM time_slots WHERE date = %s AND time = %s", (date, time_str))
    slot = cur.fetchone()
    cur.close()
    conn.close()

    if not slot or slot[0]:
        await update.message.reply_text("❌ На жаль цей слот вже зайнятий 😔\n\nВибери інший час:", reply_markup=main_menu())
        return ConversationHandler.END

    booking_id = create_booking(uid, date, time_str, name, phone, desc, contact, photos)
    dt = datetime.strptime(date + " " + time_str, "%Y-%m-%d %H:%M")
    schedule_reminder(context.application, booking_id, uid, date, time_str)
    context.user_data.clear()

    contact_text = contact or "не вказано"
    await update.message.reply_text(
        "✅ <b>Запис підтверджено!</b>\n\n📅 " + dt.strftime('%d.%m.%Y') + "\n🕐 " + time_str + "\n👤 " + name + "\n📞 " + phone + "\n📝 " + desc + "\n💬 " + contact_text + "\n\nЧекаємо на тебе! ❤️",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    try:
        admin_text = "🆕 <b>Новий запис!</b>\n\n#" + str(booking_id) + " | " + dt.strftime('%d.%m.%Y') + " о " + time_str + "\n👤 " + name + "\n📞 " + phone + "\n📝 " + desc + "\n💬 " + contact_text + "\nTelegram ID: " + str(uid)
        if photos:
            from telegram import InputMediaPhoto
            media = [InputMediaPhoto(media=photos[0], caption=admin_text, parse_mode="HTML")]
            for photo_id in photos[1:]:
                media.append(InputMediaPhoto(media=photo_id))
            await context.bot.send_media_group(chat_id=ADMIN_ID, media=media)
        else:
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode="HTML")
    except Exception as e:
        logging.error("Admin notify error: " + str(e))

    return ConversationHandler.END

async def admin_cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        await update.message.reply_text("Скасовано.", reply_markup=admin_menu())
        return ConversationHandler.END
    try:
        booking_id = int(text)
        booking = get_booking_by_id(booking_id)

        if not booking or booking['status'] != 'active':
            await update.message.reply_text("❌ Запис не знайдено або вже скасований.", reply_markup=admin_menu())
            return ConversationHandler.END

        freed_date = cancel_booking(booking_id)
        job_id = "reminder_" + str(booking_id)
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

        if freed_date:
            await notify_waitlist(context.application, freed_date, booking['time'])

        try:
            await context.bot.send_message(
                chat_id=booking["user_id"],
                text="❌ Ваш запис на " + booking['date'] + " о " + booking['time'] + " скасовано адміністратором.\n\nВибачте за незручності! Будемо раді записати вас на інший час 😊"
            )
        except:
            pass

        await update.message.reply_text("✅ Запис #" + str(booking_id) + " скасовано.\nКлієнт отримав повідомлення.", reply_markup=admin_menu())
    except ValueError:
        await update.message.reply_text("❌ Введи правильний ID (число).", reply_markup=cancel_menu())
        return WAITING_ADMIN_CANCEL_ID
    return ConversationHandler.END

async def admin_edit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Скасувати":
        await update.message.reply_text("Скасовано.", reply_markup=admin_menu())
        return ConversationHandler.END
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO booking_settings (key, value) VALUES ('price_text', %s) ON CONFLICT (key) DO UPDATE SET value = %s", (text, text))
    conn.commit()
    cur.close()
    conn.close()
    await update.message.reply_text("✅ Прайс оновлено!", reply_markup=admin_menu())
    return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Скасовано.", reply_markup=main_menu())
    return ConversationHandler.END

def restore_reminders(app):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, user_id, date, time FROM bookings WHERE status = 'active'")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        count = 0
        for r in rows:
            remind_dt = datetime.strptime(r[2] + " " + r[3], "%Y-%m-%d %H:%M") - timedelta(hours=24)
            if remind_dt > datetime.now():
                scheduler.add_job(send_reminder, "date", run_date=remind_dt, args=[app, r[1], r[3]], id="reminder_" + str(r[0]), replace_existing=True)
                count += 1
        print("Відновлено " + str(count) + " нагадувань")
    except Exception as e:
        logging.error("Restore reminders error: " + str(e))

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
            CallbackQueryHandler(booking_callback, pattern="^(?!admin_)"),
        ],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            WAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            WAITING_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_desc)],
            WAITING_PHOTO: [
                MessageHandler(filters.PHOTO, get_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_photo),
            ],
            WAITING_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_contact)],
            WAITING_ADMIN_CANCEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_cancel_booking)],
            WAITING_ADMIN_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_price)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel_command),
        ],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    app.add_handler(conv)

    scheduler.start()
    restore_reminders(app)
    print("Бот запущено!")
    app.run_polling()

if __name__ == "__main__":
    main()
