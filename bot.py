import asyncio
import logging
import io
import csv
import re
import json
import os
import shutil
from datetime import datetime, timedelta, date
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import aiosqlite

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8986719193:AAEZFDHbphJrwLUZa9BMIpoP7AUEANgyENk" 
plt.rcParams["font.family"] = "DejaVu Sans"

DB_PATH = "bot_data.db"
CHECKPOINT_DIR = "checkpoints"
AUTOSAVE_MINUTES = 5

RELATIONSHIP_START = date(2025, 11, 10)

os.makedirs(CHECKPOINT_DIR, exist_ok=True)

# ==================== ДАННЫЕ ====================
users_data = {
    1: {"name": "Игрок 1", "balance": 5000, "history": []},
    2: {"name": "Игрок 2", "balance": 3000, "history": []},
}

shopping_list = {}
shopping_counter = 0

events_data = {}
event_counter = 0

notes_data = {}
note_counter = 0

balance_history = [(datetime.now(), users_data[1]["balance"] + users_data[2]["balance"])]

notifications = {}
notif_counter = 0

recurring_payments = {}
recurring_counter = 0

nav_stack = {}

# ==================== СОСТОЯНИЯ ====================
class MoneyStates(StatesGroup):
    waiting_for_amount = State()

class ShoppingStates(StatesGroup):
    waiting_for_item = State()

class EventStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_date = State()
    waiting_for_edit_value = State()

class NoteStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_text = State()
    waiting_for_edit_value = State()

class CountdownStates(StatesGroup):
    waiting_for_date = State()
    waiting_for_name = State()

class NotifStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_custom_date = State()
    waiting_for_time = State()

class RecurringStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_interval = State()
    waiting_for_day = State()
    waiting_for_time = State()

class HistoryStates(StatesGroup):
    waiting_for_min_amount = State()

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== НАВИГАЦИЯ ====================

def push_screen(chat_id: int, screen: str):
    stack = nav_stack.setdefault(chat_id, ["main"])
    if stack[-1] != screen:
        stack.append(screen)
    if len(stack) > 20:
        stack.pop(0)

def pop_screen(chat_id: int) -> str:
    stack = nav_stack.setdefault(chat_id, ["main"])
    if len(stack) > 1:
        stack.pop()
    return stack[-1]

def reset_stack(chat_id: int):
    nav_stack[chat_id] = ["main"]

# ==================== БАЗА ДАННЫХ ====================

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, balance INTEGER)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, player_id INTEGER, date TEXT, amount INTEGER, sign TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS shopping (id INTEGER PRIMARY KEY, text TEXT, done INTEGER, added_by TEXT, date TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, text TEXT, date TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, title TEXT, text TEXT, date TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY, text TEXT, date TEXT, time TEXT, chat_id INTEGER, sent INTEGER, category TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS recurring (id INTEGER PRIMARY KEY, text TEXT, interval TEXT, day INTEGER, time TEXT, chat_id INTEGER, last_sent TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS balance_history (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, total INTEGER)""")
        await db.commit()

    async with aiosqlite.connect(DB_PATH) as db:
        # Автомиграции
        async with db.execute("PRAGMA table_info(notifications)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
        if "category" not in cols:
            await db.execute("ALTER TABLE notifications ADD COLUMN category TEXT DEFAULT 'once'")
            await db.commit()

        async with db.execute("PRAGMA table_info(recurring)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
        if "interval" not in cols:
            await db.execute("ALTER TABLE recurring ADD COLUMN interval TEXT DEFAULT 'monthly'")
            await db.commit()

        async with db.execute("PRAGMA table_info(shopping)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
        if "date" not in cols:
            await db.execute("ALTER TABLE shopping ADD COLUMN date TEXT DEFAULT ''")
            await db.commit()

async def save_to_db():
    async with aiosqlite.connect(DB_PATH) as db:
        for pid, p in users_data.items():
            await db.execute("INSERT OR REPLACE INTO users (id, name, balance) VALUES (?, ?, ?)",
                             (pid, p["name"], p["balance"]))
        await db.execute("DELETE FROM history")
        for pid, p in users_data.items():
            for r in p["history"]:
                await db.execute("INSERT INTO history (player_id, date, amount, sign) VALUES (?, ?, ?, ?)",
                                 (pid, r["date"].isoformat(), r["amount"], r["sign"]))
        await db.execute("DELETE FROM shopping")
        for sid, item in shopping_list.items():
            await db.execute("INSERT INTO shopping (id, text, done, added_by, date) VALUES (?, ?, ?, ?, ?)",
                             (sid, item["text"], 1 if item["done"] else 0, item.get("added_by", ""), item.get("date", "")))
        await db.execute("DELETE FROM events")
        for eid, e in events_data.items():
            await db.execute("INSERT INTO events (id, text, date) VALUES (?, ?, ?)",
                             (eid, e["text"], e["date"]))
        await db.execute("DELETE FROM notes")
        for nid, n in notes_data.items():
            await db.execute("INSERT INTO notes (id, title, text, date) VALUES (?, ?, ?, ?)",
                             (nid, n["title"], n["text"], n["date"]))
        await db.execute("DELETE FROM notifications")
        for nid, n in notifications.items():
            await db.execute("INSERT INTO notifications (id, text, date, time, chat_id, sent, category) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (nid, n["text"], n["date"], n["time"], n["chat_id"], 1 if n.get("sent") else 0, n.get("category", "once")))
        await db.execute("DELETE FROM recurring")
        for rid, r in recurring_payments.items():
            await db.execute("INSERT INTO recurring (id, text, interval, day, time, chat_id, last_sent) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (rid, r["text"], r.get("interval", "monthly"), r.get("day", 0), r["time"], r["chat_id"], r.get("last_sent", "")))
        await db.execute("DELETE FROM balance_history")
        for dt, total in balance_history:
            await db.execute("INSERT INTO balance_history (date, total) VALUES (?, ?)",
                             (dt.isoformat(), total))
        await db.commit()

async def load_from_db():
    global shopping_counter, event_counter, notif_counter, recurring_counter, note_counter
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, name, balance FROM users") as cursor:
            async for pid, name, balance in cursor:
                if pid in users_data:
                    users_data[pid]["name"] = name
                    users_data[pid]["balance"] = balance

        for pid in users_data:
            users_data[pid]["history"] = []
        async with db.execute("SELECT player_id, date, amount, sign FROM history ORDER BY id") as cursor:
            async for pid, date_str, amount, sign in cursor:
                if pid in users_data:
                    users_data[pid]["history"].append({
                        "date": datetime.fromisoformat(date_str),
                        "amount": amount, "sign": sign,
                        "player": users_data[pid]["name"], "player_id": pid,
                    })

        shopping_list.clear()
        async with db.execute("SELECT id, text, done, added_by, date FROM shopping") as cursor:
            async for sid, text, done, added_by, date_str in cursor:
                shopping_list[sid] = {"text": text, "done": bool(done), "added_by": added_by, "date": date_str}
                if sid > shopping_counter:
                    shopping_counter = sid

        events_data.clear()
        async with db.execute("SELECT id, text, date FROM events") as cursor:
            async for eid, text, date_str in cursor:
                events_data[eid] = {"text": text, "date": date_str}
                if eid > event_counter:
                    event_counter = eid

        notes_data.clear()
        async with db.execute("SELECT id, title, text, date FROM notes") as cursor:
            async for nid, title, text, date_str in cursor:
                notes_data[nid] = {"title": title, "text": text, "date": date_str}
                if nid > note_counter:
                    note_counter = nid

        notifications.clear()
        async with db.execute("SELECT id, text, date, time, chat_id, sent, category FROM notifications") as cursor:
            async for nid, text, date_str, time_str, chat_id, sent, category in cursor:
                notifications[nid] = {"text": text, "date": date_str, "time": time_str,
                                       "chat_id": chat_id, "sent": bool(sent), "category": category or "once"}
                if nid > notif_counter:
                    notif_counter = nid

        recurring_payments.clear()
        async with db.execute("SELECT id, text, interval, day, time, chat_id, last_sent FROM recurring") as cursor:
            async for rid, text, interval, day, time_str, chat_id, last_sent in cursor:
                recurring_payments[rid] = {"text": text, "interval": interval or "monthly",
                                            "day": day, "time": time_str,
                                            "chat_id": chat_id, "last_sent": last_sent}
                if rid > recurring_counter:
                    recurring_counter = rid

        balance_history.clear()
        async with db.execute("SELECT date, total FROM balance_history ORDER BY id") as cursor:
            async for date_str, total in cursor:
                balance_history.append((datetime.fromisoformat(date_str), total))
        if not balance_history:
            balance_history.append((datetime.now(), users_data[1]["balance"] + users_data[2]["balance"]))

# ==================== ЧЕКПОИНТЫ ====================

def make_checkpoint(name: str = None) -> str:
    if not name:
        name = datetime.now().strftime("%Y%m%d_%H%M%S")
    cp_path = os.path.join(CHECKPOINT_DIR, name)
    os.makedirs(cp_path, exist_ok=True)
    if os.path.exists(DB_PATH):
        shutil.copy(DB_PATH, os.path.join(cp_path, "bot_data.db"))
    snapshot = {
        "users": {str(pid): {"name": p["name"], "balance": p["balance"]} for pid, p in users_data.items()},
        "shopping": {str(sid): item for sid, item in shopping_list.items()},
        "events": {str(eid): e for eid, e in events_data.items()},
        "notes": {str(nid): n for nid, n in notes_data.items()},
        "notifications": {str(nid): n for nid, n in notifications.items()},
        "recurring": {str(rid): r for rid, r in recurring_payments.items()},
        "balance_history": [(dt.isoformat(), total) for dt, total in balance_history],
        "created_at": datetime.now().isoformat(),
    }
    with open(os.path.join(cp_path, "snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return name

async def autosave_loop():
    while True:
        await asyncio.sleep(AUTOSAVE_MINUTES * 60)
        try:
            await save_to_db()
        except Exception as e:
            logging.error(f"Ошибка автосохранения: {e}")

# ==================== НИЖНЯЯ ПАНЕЛЬ ====================

def bottom_panel_kb():
    b = ReplyKeyboardBuilder()
    b.button(text="⬅️ Назад")
    return b.as_markup(resize_keyboard=True)

# ==================== СЧЁТЧИКИ ДАТ ====================

def get_days_together() -> int:
    today = date.today()
    return (today - RELATIONSHIP_START).days


def get_anniversary_dates():
    today = date.today()
    candidates = []
    for year_offset in range(0, 10):
        y = RELATIONSHIP_START.year + year_offset
        for month_offset in (0, 6):
            m = RELATIONSHIP_START.month + month_offset
            y_adj = y + (m - 1) // 12
            m_adj = (m - 1) % 12 + 1
            try:
                d = date(y_adj, m_adj, RELATIONSHIP_START.day)
            except ValueError:
                continue
            if d > today:
                candidates.append(d)
    candidates.sort()
    half_anniv = None
    full_anniv = None
    for c in candidates:
        years = c.year - RELATIONSHIP_START.year
        months = (c.month - RELATIONSHIP_START.month) + years * 12
        if months % 12 == 6 and half_anniv is None:
            half_anniv = c
        elif months % 12 == 0 and full_anniv is None:
            full_anniv = c
        if half_anniv and full_anniv:
            break
    return half_anniv, full_anniv


def get_counters_text() -> str:
    today = date.today()
    days = get_days_together()
    half, full = get_anniversary_dates()
    text = f"💕 <b>Вы вместе:</b> {days} дней\n"
    if half:
        days_to_half = (half - today).days
        text += f"🎉 <b>До полугодовщины:</b> {days_to_half} дн. ({half.strftime('%d.%m.%Y')})\n"
    if full:
        days_to_full = (full - today).days
        text += f"🎂 <b>До годовщины:</b> {days_to_full} дн. ({full.strftime('%d.%m.%Y')})\n"
    return text

# ==================== ПАРСЕР ТЕКСТА ====================

WORD_NUMBERS = {
    "ноль": 0, "нуль": 0, "один": 1, "одна": 1, "одну": 1, "единица": 1, "первый": 1,
    "два": 2, "две": 2, "двух": 2, "второй": 2, "три": 3, "трёх": 3, "трех": 3, "третий": 3,
    "четыре": 4, "четырёх": 4, "четырех": 4, "четвертый": 4, "пять": 5, "пяти": 5, "пятый": 5,
    "шесть": 6, "шести": 6, "шестой": 6, "семь": 7, "семи": 7, "седьмой": 7,
    "восемь": 8, "восьми": 8, "восьмой": 8, "девять": 9, "девяти": 9, "девятый": 9,
    "десять": 10, "десяти": 10, "десятый": 10, "двадцать": 20, "тридцать": 30,
    "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80,
    "девяносто": 90, "сто": 100, "сотня": 100, "сотню": 100, "двести": 200, "триста": 300,
    "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800,
    "девятьсот": 900, "тысяча": 1000, "тысячу": 1000, "тыща": 1000, "тысяч": 1000,
    "косарь": 1000, "косаря": 1000, "штука": 1000, "штуку": 1000, "пятихатка": 500,
    "пятихатку": 500, "червонец": 10, "сотка": 100, "сотку": 100, "полтинник": 50,
}

ADD_WORDS = ["добав", "прибав", "начисл", "пополн", "внес", "внести", "плюс", "+",
             "закин", "накин", "подкин", "прикин", "полож", "зачисл",
             "перевед", "перевел", "перевести", "дай", "давай", "насып", "накидай"]
SUB_WORDS = ["убери", "убрать", "убир", "спиш", "списать", "спис", "вычт", "вычесть",
             "минус", "-", "сним", "снять", "забер", "забрать", "отбер", "отобрать",
             "отня", "отними", "отнять", "уменш", "уменьш", "сократ", "потрат", "потратил",
             "израсход", "расход"]
TASK_WORDS = ["задач", "дел", "дело", "поручен", "напомин", "todo", "to-do"]

def parse_amount(text_lower: str):
    numbers = re.findall(r'\d+', text_lower)
    if numbers:
        return int(numbers[-1])
    sorted_words = sorted(WORD_NUMBERS.keys(), key=len, reverse=True)
    for word in sorted_words:
        if word in text_lower:
            return WORD_NUMBERS[word]
    return None

def parse_text_command(text: str):
    if not text:
        return None
    text_lower = text.lower().strip()
    amount = parse_amount(text_lower)

    player_id = None
    if any(w in text_lower for w in ["игроку 1", "игрока 1", "игрок 1", "игроке 1",
                                       "первому", "первого", "первый игрок",
                                       "первому игроку", "у первого"]):
        player_id = 1
    elif any(w in text_lower for w in ["игроку 2", "игрока 2", "игрок 2", "игроке 2",
                                         "второму", "второго", "второй игрок",
                                         "второму игроку", "у второго"]):
        player_id = 2

    if any(w in text_lower for w in TASK_WORDS):
        if any(w in text_lower for w in ["добав", "созда", "нов", "постав", "запиш", "запиши"]):
            task_text = text
            for trigger in ["задачу", "задача", "задач", "дело", "поручение", "напоминание"]:
                idx = text_lower.find(trigger)
                if idx != -1:
                    task_text = text[idx + len(trigger):].strip()
                    break
            if not task_text:
                task_text = "Без названия"
            return ("add_task", {"text": task_text})
        if any(w in text_lower for w in ["покаж", "список", "какие", "что", "перечисл", "вывед"]):
            return ("show_tasks", {})

    if any(w in text_lower for w in ["общак", "баланс", "сколько", "денег", "деньги", "сумм",
                                       "итог", "всего", "накоплен", "состоян"]):
        if any(w in text_lower for w in ["покаж", "сколько", "какой", "скажи", "вывед"]):
            return ("show_balance", {})
        if "общак" in text_lower or "всего" in text_lower:
            return ("show_balance", {})

    if player_id and amount is not None:
        if any(w in text_lower for w in ADD_WORDS):
            return ("add_money", {"player_id": player_id, "amount": amount})
        if any(w in text_lower for w in SUB_WORDS):
            return ("sub_money", {"player_id": player_id, "amount": amount})

    return None

# ==================== ГРАФИКИ ====================

def make_balances_chart() -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(6, 4))
    names = [users_data[1]["name"], users_data[2]["name"]]
    values = [users_data[1]["balance"], users_data[2]["balance"]]
    bars = ax.bar(names, values, color=["#4C9AFF", "#FFAB49"], edgecolor="black", linewidth=0.7)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values) * 0.02,
                f"{v}", ha="center", va="bottom", fontsize=12, fontweight="bold")
    ax.set_title("Текущие балансы", fontsize=14, fontweight="bold")
    ax.set_ylabel("Сумма")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf

def make_history_chart() -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(7, 4))
    by_date = {}
    for dt, total in balance_history:
        by_date[dt.strftime("%d.%m.%Y")] = total
    dates = list(by_date.keys())
    totals = list(by_date.values())
    x = range(len(dates))
    ax.plot(x, totals, marker="o", color="#2E8B57", linewidth=2, markersize=7)
    ax.fill_between(x, totals, alpha=0.15, color="#2E8B57")
    ax.set_xticks(list(x))
    ax.set_xticklabels(dates, rotation=45, ha="right", fontsize=9)
    ax.set_title("Динамика общего баланса", fontsize=14, fontweight="bold")
    ax.set_ylabel("Всего")
    ax.grid(linestyle="--", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf

# ==================== ИСТОРИЯ ====================

def get_all_records():
    records = []
    for pid, p in users_data.items():
        for r in p["history"]:
            records.append(r)
    records.sort(key=lambda x: x["date"], reverse=True)
    return records

def filter_records(records, filter_type="all", date_filter="all", player_filter="all", min_amount=None):
    now = datetime.now()
    result = []
    for r in records:
        if filter_type == "add" and r["sign"] != "+":
            continue
        if filter_type == "sub" and r["sign"] != "-":
            continue
        if player_filter != "all" and r["player_id"] != int(player_filter):
            continue
        if date_filter == "today":
            if r["date"].date() != now.date():
                continue
        elif date_filter == "week":
            if r["date"] < now - timedelta(days=7):
                continue
        elif date_filter == "month":
            if r["date"] < now - timedelta(days=30):
                continue
        if min_amount is not None and abs(r["amount"]) < min_amount:
            continue
        result.append(r)
    return result

def format_records_page(records, page: int, per_page: int = 10):
    total_pages = max(1, (len(records) + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    end = start + per_page
    page_records = records[start:end]
    if not records:
        return "📜 <b>История операций</b>\n\n<i>Записей по фильтру не найдено.</i>", page, total_pages
    text = f"📜 <b>История операций</b> (стр. {page}/{total_pages})\n"
    text += f"Всего записей: <b>{len(records)}</b>\n"
    text += "━━━━━━━━━━━━━━━\n"
    for r in page_records:
        date_str = r["date"].strftime("%d.%m.%Y")
        emoji = "🟢" if r["sign"] == "+" else "🔴"
        text += f"{emoji} {date_str} — {r['player']}: {r['sign']}{r['amount']}\n"
    return text, page, total_pages

def make_history_csv(records) -> io.BytesIO:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Дата", "Игрок", "Операция", "Сумма"])
    for r in records:
        writer.writerow([r["date"].strftime("%d.%m.%Y"), r["player"],
                         "Пополнение" if r["sign"] == "+" else "Списание", r["amount"]])
    result = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    result.seek(0)
    return result

# ==================== КЛАВИАТУРЫ ====================

def main_menu_kb():
    b = InlineKeyboardBuilder()
    b.button(text="💰 Финансы", callback_data="menu_finance")
    b.button(text="🛒 Список покупок", callback_data="menu_shopping")
    b.button(text="📅 Календарь", callback_data="menu_calendar")
    b.button(text="📝 Заметки", callback_data="menu_notes")
    b.button(text="⏳ Отсчёт", callback_data="menu_countdown")
    b.button(text="📊 Данные", callback_data="menu_data")
    b.button(text="🔔 Уведомления", callback_data="menu_notif")
    b.adjust(2, 2, 2, 1)
    return b.as_markup()

# --- Список покупок ---

def shopping_menu_kb():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить покупку", callback_data="shopping_add")
    b.button(text="🗑 Удалить", callback_data="shopping_delete_start")
    b.adjust(1, 1)
    return b.as_markup()

def shopping_numbers_kb(action: str):
    b = InlineKeyboardBuilder()
    items = list(shopping_list.items())
    for idx, (sid, item) in enumerate(items, start=1):
        b.button(text=f"{idx}", callback_data=f"{action}_shopping_{sid}")
    b.button(text="⬅️ Отмена", callback_data="menu_shopping")
    if items:
        b.adjust(*([5] * ((len(items) + 4) // 5)), 1)
    else:
        b.adjust(1)
    return b.as_markup()

# --- События ---

def calendar_menu_kb():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить", callback_data="event_add")
    b.button(text="✏️ Изменить", callback_data="event_edit_start")
    b.button(text="🗑 Удалить", callback_data="event_delete_start")
    b.adjust(1, 1, 1)
    return b.as_markup()

def event_numbers_kb(action: str, items: dict):
    b = InlineKeyboardBuilder()
    for idx, eid in enumerate(items.keys(), start=1):
        b.button(text=f"{idx}", callback_data=f"{action}_event_{eid}")
    b.button(text="⬅️ Отмена", callback_data="menu_calendar")
    b.adjust(*([5] * ((len(items) + 4) // 5)), 1)
    return b.as_markup()

# --- Заметки ---

def notes_menu_kb():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить", callback_data="note_add")
    b.button(text="✏️ Изменить", callback_data="note_edit_start")
    b.button(text="🗑 Удалить", callback_data="note_delete_start")
    b.adjust(1, 1, 1)
    return b.as_markup()

def note_numbers_kb(action: str, items: dict):
    b = InlineKeyboardBuilder()
    for idx, nid in enumerate(items.keys(), start=1):
        b.button(text=f"{idx}", callback_data=f"{action}_note_{nid}")
    b.button(text="⬅️ Отмена", callback_data="menu_notes")
    b.adjust(*([5] * ((len(items) + 4) // 5)), 1)
    return b.as_markup()

# --- Отсчёт ---

def countdown_menu_kb():
    b = InlineKeyboardBuilder()
    today = date.today()
    for cid, c in events_data.items():
        try:
            target = datetime.strptime(c["date"], "%d.%m.%Y").date()
            days_left = (target - today).days
            if days_left >= 0:
                short = c["text"][:25] + ("..." if len(c["text"]) > 25 else "")
                b.button(text=f"⏳ {days_left} дн. — {short}", callback_data=f"view_countdown_{cid}")
        except Exception:
            pass
    b.button(text="➕ Добавить отсчёт", callback_data="countdown_add")
    b.adjust(*([1] * len(events_data)), 1)
    return b.as_markup()

# --- Данные ---

def data_menu_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📊 Балансы игроков", callback_data="chart_balances")
    b.button(text="📈 Динамика", callback_data="chart_history")
    b.adjust(1, 1)
    return b.as_markup()

# --- Уведомления ---

def notif_menu_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📌 Разовые", callback_data="notif_category_once")
    b.button(text="🔁 Повторяющиеся", callback_data="notif_category_recurring")
    b.adjust(1, 1)
    return b.as_markup()

def notif_once_kb():
    b = InlineKeyboardBuilder()
    for nid, n in notifications.items():
        if n.get("category") == "once" and not n.get("sent"):
            short = n["text"][:20] + ("..." if len(n["text"]) > 20 else "")
            b.button(text=f"🔔 {n['date']} {n['time']} — {short}", callback_data=f"del_notif_{nid}")
    b.button(text="➕ Создать разовое", callback_data="notif_add")
    b.button(text="⬅️ Назад", callback_data="menu_notif")
    b.adjust(*([1] * len([n for n in notifications.values() if n.get("category") == "once" and not n.get("sent")])), 1, 1)
    return b.as_markup()

def notif_recurring_kb():
    b = InlineKeyboardBuilder()
    for rid, r in recurring_payments.items():
        short = r["text"][:20] + ("..." if len(r["text"]) > 20 else "")
        interval = r.get("interval", "monthly")
        if interval == "daily":
            label = f"🔁 Каждый день {r['time']} — {short}"
        elif interval == "weekly":
            weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            day_name = weekdays[r.get("day", 0) % 7]
            label = f"🔁 Каждую {day_name} {r['time']} — {short}"
        else:
            label = f"🔁 {r['day']:02d} числа {r['time']} — {short}"
        b.button(text=label, callback_data=f"del_recurring_{rid}")
    b.button(text="➕ Создать повторяющееся", callback_data="recurring_add")
    b.button(text="⬅️ Назад", callback_data="menu_notif")
    b.adjust(*([1] * len(recurring_payments)), 1, 1)
    return b.as_markup()

def notif_date_kb():
    b = InlineKeyboardBuilder()
    today = datetime.now()
    b.button(text="Сегодня", callback_data=f"nsetdate_{today.strftime('%d.%m.%Y')}")
    b.button(text="Завтра", callback_data=f"nsetdate_{(today + timedelta(days=1)).strftime('%d.%m.%Y')}")
    b.button(text="Через 3 дня", callback_data=f"nsetdate_{(today + timedelta(days=3)).strftime('%d.%m.%Y')}")
    b.button(text="Через неделю", callback_data=f"nsetdate_{(today + timedelta(days=7)).strftime('%d.%m.%Y')}")
    b.button(text="📅 Ввести вручную", callback_data="nsetdate_custom")
    b.button(text="❌ Отмена", callback_data="menu_notif")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()

def recurring_interval_kb():
    b = InlineKeyboardBuilder()
    b.button(text="Каждый день", callback_data="rec_daily")
    b.button(text="Каждую неделю", callback_data="rec_weekly")
    b.button(text="Каждый месяц", callback_data="rec_monthly")
    b.button(text="❌ Отмена", callback_data="notif_category_recurring")
    b.adjust(1, 1, 1, 1)
    return b.as_markup()

def recurring_weekday_kb():
    b = InlineKeyboardBuilder()
    weekdays = [("Пн", 0), ("Вт", 1), ("Ср", 2), ("Чт", 3), ("Пт", 4), ("Сб", 5), ("Вс", 6)]
    for name, idx in weekdays:
        b.button(text=name, callback_data=f"rec_day_{idx}")
    b.button(text="❌ Отмена", callback_data="notif_category_recurring")
    b.adjust(4, 3, 1)
    return b.as_markup()

# --- Финансы ---

def finance_menu_kb():
    b = InlineKeyboardBuilder()
    b.button(text="👤 Игрок 1", callback_data="fin_player_1")
    b.button(text="👤 Игрок 2", callback_data="fin_player_2")
    b.button(text="📜 История", callback_data="hist_open")
    b.adjust(2, 1)
    return b.as_markup()

def player_actions_kb(player_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="➕ Внести", callback_data=f"add_{player_id}")
    b.button(text="➖ Убрать", callback_data=f"sub_{player_id}")
    b.adjust(2)
    return b.as_markup()

def cancel_kb(target="main_menu"):
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data=target)
    return b.as_markup()

def history_filters_kb(state_data: dict, page: int, total_pages: int):
    b = InlineKeyboardBuilder()
    if total_pages > 1:
        if page > 1:
            b.button(text="⬅️", callback_data=f"hist_page_{page-1}")
        b.button(text=f"{page}/{total_pages}", callback_data="hist_noop")
        if page < total_pages:
            b.button(text="➡️", callback_data=f"hist_page_{page+1}")
        b.adjust(3)
    t = state_data.get("filter_type", "all")
    b.button(text=("✅ " if t == "all" else "") + "Все", callback_data="hist_set_all")
    b.button(text=("✅ " if t == "add" else "") + "🟢 Пополнения", callback_data="hist_set_add")
    b.button(text=("✅ " if t == "sub" else "") + "🔴 Списания", callback_data="hist_set_sub")
    b.adjust(3)
    p = state_data.get("player_filter", "all")
    b.button(text=("✅ " if p == "all" else "") + "Все игроки", callback_data="hist_player_all")
    b.button(text=("✅ " if p == "1" else "") + "Игрок 1", callback_data="hist_player_1")
    b.button(text=("✅ " if p == "2" else "") + "Игрок 2", callback_data="hist_player_2")
    b.adjust(3)
    d = state_data.get("date_filter", "all")
    b.button(text=("✅ " if d == "today" else "") + "Сегодня", callback_data="hist_date_today")
    b.button(text=("✅ " if d == "week" else "") + "Неделя", callback_data="hist_date_week")
    b.button(text=("✅ " if d == "month" else "") + "Месяц", callback_data="hist_date_month")
    b.button(text=("✅ " if d == "all" else "") + "Всё время", callback_data="hist_date_all")
    b.adjust(4)
    min_amt = state_data.get("min_amount")
    search_label = f"🔍 Сумма ≥ {min_amt}" if min_amt else "🔍 Поиск по сумме"
    b.button(text=search_label, callback_data="hist_search_amount")
    b.button(text="📥 Экспорт в CSV", callback_data="hist_export")
    b.adjust(2)
    b.button(text="🔄 Сбросить фильтры", callback_data="hist_reset")
    b.adjust(1)
    return b.as_markup()

# ==================== ТЕКСТЫ ====================

def get_start_text():
    total = users_data[1]["balance"] + users_data[2]["balance"]
    counters = get_counters_text()
    return (
        counters + "\n"
        "💰 <b>Финансы</b>\n"
        f"   • {users_data[1]['name']}: <b>{users_data[1]['balance']}</b>\n"
        f"   • {users_data[2]['name']}: <b>{users_data[2]['balance']}</b>\n"
        f"   • <b>Всего: {total}</b>\n\n"
        f"🛒 В списке покупок: <b>{len(shopping_list)}</b>\n"
        f"📅 Событий: <b>{len(events_data)}</b>\n"
        f"📝 Заметок: <b>{len(notes_data)}</b>\n"
        f"🔔 Уведомлений: <b>{len(notifications) + len(recurring_payments)}</b>"
    )

def get_finance_text():
    total = users_data[1]["balance"] + users_data[2]["balance"]
    return (
        "💰 <b>Финансы</b>\n\n"
        f"👤 {users_data[1]['name']}: <b>{users_data[1]['balance']}</b>\n"
        f"👤 {users_data[2]['name']}: <b>{users_data[2]['balance']}</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"<b>Всего: {total}</b>"
    )

def get_player_text(pid: int):
    p = users_data[pid]
    return (f"👤 <b>{p['name']}</b>\n\n💵 Баланс: <b>{p['balance']}</b>\n"
            f"📜 Записей в истории: {len(p['history'])}")

def get_shopping_text():
    if not shopping_list:
        return ("🛒 <b>Список покупок</b>\n\n<i>Список пуст.</i>\n\n"
                "Нажмите «➕ Добавить покупку», чтобы добавить.")
    text = "🛒 <b>Список покупок</b>\n\n"
    for idx, (sid, item) in enumerate(shopping_list.items(), start=1):
        date_short = ""
        if item.get("date"):
            try:
                dt = datetime.strptime(item["date"], "%d.%m.%Y")
                date_short = dt.strftime("%d.%m") + " — "
            except Exception:
                date_short = item["date"] + " — "
        text += f"<b>{idx}.</b> {date_short}{item['text']}\n"
    text += "\nВыберите действие кнопками ниже."
    return text

def get_shopping_select_text(action: str):
    return "🗑 <b>Удаление покупки</b>\n\n" + get_shopping_text() + "\n\nВыберите номер покупки:"

def get_calendar_text():
    if not events_data:
        return ("📅 <b>Календарь событий</b>\n\n<i>Событий пока нет.</i>\n\n"
                "Нажмите «➕ Добавить», чтобы создать.")
    text = "📅 <b>Календарь событий</b>\n\n"
    sorted_items = sorted(events_data.items(), key=lambda x: datetime.strptime(x[1]["date"], "%d.%m.%Y"))
    for idx, (eid, e) in enumerate(sorted_items, start=1):
        text += f"<b>{idx}.</b> {e['date']} — {e['text']}\n"
    text += "\nВыберите действие кнопками ниже."
    return text

def get_calendar_select_text(action: str):
    if action == "edit":
        return "✏️ <b>Изменение события</b>\n\n" + get_calendar_text() + "\n\nВыберите номер события:"
    return "🗑 <b>Удаление события</b>\n\n" + get_calendar_text() + "\n\nВыберите номер события:"

def get_notes_text():
    if not notes_data:
        return ("📝 <b>Заметки</b>\n\n<i>Заметок пока нет.</i>\n\n"
                "Нажмите «➕ Добавить», чтобы создать.")
    text = "📝 <b>Заметки</b>\n\n"
    for idx, (nid, n) in enumerate(notes_data.items(), start=1):
        text += f"<b>{idx}.</b> {n['title']}\n"
    text += "\nВыберите действие кнопками ниже."
    return text

def get_notes_select_text(action: str):
    if action == "edit":
        return "✏️ <b>Изменение заметки</b>\n\n" + get_notes_text() + "\n\nВыберите номер заметки:"
    return "🗑 <b>Удаление заметки</b>\n\n" + get_notes_text() + "\n\nВыберите номер заметки:"

def get_note_text(note_id: int):
    n = notes_data.get(note_id)
    if not n:
        return "⚠️ Заметка не найдена."
    return (f"📝 <b>{n['title']}</b>\n\n"
            f"{n['text']}\n\n"
            f"<i>Создано: {n['date']}</i>")

def get_countdown_text():
    if not events_data:
        return ("⏳ <b>Обратный отсчёт</b>\n\n<i>Событий пока нет.</i>\n\n"
                "Нажмите «➕ Добавить отсчёт», чтобы создать.")
    text = "⏳ <b>Обратный отсчёт</b>\n\n"
    today = date.today()
    for cid, c in events_data.items():
        try:
            target = datetime.strptime(c["date"], "%d.%m.%Y").date()
            days_left = (target - today).days
            if days_left > 0:
                text += f"⏳ <b>{c['text']}</b> — через <b>{days_left}</b> дн. ({c['date']})\n"
            elif days_left == 0:
                text += f"🎉 <b>{c['text']}</b> — <b>сегодня!</b>\n"
            else:
                text += f"✅ <b>{c['text']}</b> — прошло {-days_left} дн. назад\n"
        except Exception:
            pass
    return text

def get_data_text():
    return (f"📊 <b>Данные</b>\n\nВыберите, что показать:\n\n"
            f"💰 Всего: <b>{users_data[1]['balance'] + users_data[2]['balance']}</b>\n"
            f"📈 Точек на графике: {len(balance_history)}")

def get_notif_text():
    once_count = len([n for n in notifications.values() if n.get("category") == "once" and not n.get("sent")])
    recurring_count = len(recurring_payments)
    return (
        "🔔 <b>Уведомления</b>\n\n"
        f"📌 Разовые: <b>{once_count}</b>\n"
        f"🔁 Повторяющиеся: <b>{recurring_count}</b>\n\n"
        "Выберите категорию:"
    )

def get_notif_once_text():
    active = [n for n in notifications.values() if n.get("category") == "once" and not n.get("sent")]
    if not active:
        return ("📌 <b>Разовые уведомления</b>\n\n"
                "<i>Нет активных уведомлений.</i>\n\n"
                "Нажмите «➕ Создать разовое», чтобы добавить.")
    text = "📌 <b>Разовые уведомления</b>\n\n"
    for nid, n in notifications.items():
        if n.get("category") == "once" and not n.get("sent"):
            text += f"• <b>{n['date']} {n['time']}</b> — {n['text']}\n"
    text += "\nНажмите на уведомление, чтобы удалить."
    return text

def get_notif_recurring_text():
    if not recurring_payments:
        return ("🔁 <b>Повторяющиеся уведомления</b>\n\n"
                "<i>Нет активных уведомлений.</i>\n\n"
                "Нажмите «➕ Создать повторяющееся», чтобы добавить.")
    text = "🔁 <b>Повторяющиеся уведомления</b>\n\n"
    for rid, r in recurring_payments.items():
        interval = r.get("interval", "monthly")
        if interval == "daily":
            label = "каждый день"
        elif interval == "weekly":
            weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            label = f"каждую {weekdays[r.get('day', 0) % 7]}"
        else:
            label = f"{r['day']:02d} числа месяца"
        text += f"• <b>{label} в {r['time']}</b> — {r['text']}\n"
    text += "\nНажмите на уведомление, чтобы удалить."
    return text

# ==================== ОБРАБОТЧИКИ ====================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    reset_stack(message.chat.id)
    await message.answer(get_start_text(), reply_markup=main_menu_kb(), parse_mode="HTML")
    await message.answer("👇 Кнопка навигации:", reply_markup=bottom_panel_kb())

# --- КНОПКА «НАЗАД» ---

@dp.message(F.text == "⬅️ Назад")
async def panel_back(message: types.Message, state: FSMContext):
    await state.clear()
    chat_id = message.chat.id
    screen = pop_screen(chat_id)

    if screen == "finance":
        await message.answer(get_finance_text(), reply_markup=finance_menu_kb(), parse_mode="HTML")
    elif screen == "shopping":
        await message.answer(get_shopping_text(), reply_markup=shopping_menu_kb(), parse_mode="HTML")
    elif screen == "calendar":
        await message.answer(get_calendar_text(), reply_markup=calendar_menu_kb(), parse_mode="HTML")
    elif screen == "notes":
        await message.answer(get_notes_text(), reply_markup=notes_menu_kb(), parse_mode="HTML")
    elif screen == "countdown":
        await message.answer(get_countdown_text(), reply_markup=countdown_menu_kb(), parse_mode="HTML")
    elif screen == "notif":
        await message.answer(get_notif_text(), reply_markup=notif_menu_kb(), parse_mode="HTML")
    elif screen == "data":
        await message.answer(get_data_text(), reply_markup=data_menu_kb(), parse_mode="HTML")
    elif screen == "history":
        await message.answer(get_finance_text(), reply_markup=finance_menu_kb(), parse_mode="HTML")
    else:
        await message.answer(get_start_text(), reply_markup=main_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "main_menu")
async def back_to_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    reset_stack(callback.message.chat.id)
    await callback.answer()
    await callback.message.edit_text(get_start_text(), reply_markup=main_menu_kb(), parse_mode="HTML")

# ==================== СПИСОК ПОКУПОК ====================

@dp.callback_query(F.data == "menu_shopping")
async def show_shopping(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    push_screen(callback.message.chat.id, "shopping")
    await callback.answer()
    await callback.message.edit_text(get_shopping_text(), reply_markup=shopping_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "shopping_add")
async def shopping_add(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ShoppingStates.waiting_for_item)
    await callback.message.edit_text(
        "🛒 <b>Новая покупка</b>\n\nВведите название:\n\n" + get_shopping_text(),
        reply_markup=cancel_kb("menu_shopping"), parse_mode="HTML")

@dp.message(ShoppingStates.waiting_for_item)
async def shopping_got_item(message: types.Message, state: FSMContext):
    global shopping_counter
    text = message.text.strip()
    if not text:
        await message.answer("⚠️ Название не может быть пустым.")
        return
    shopping_counter += 1
    shopping_list[shopping_counter] = {
        "text": text,
        "done": False,
        "added_by": message.from_user.first_name,
        "date": datetime.now().strftime("%d.%m.%Y"),
    }
    await save_to_db()
    await state.clear()
    push_screen(message.chat.id, "shopping")
    await message.answer(f"✅ Добавлено: <b>{text}</b>\n\n" + get_shopping_text(),
                         reply_markup=shopping_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "shopping_delete_start")
async def shopping_delete_start(callback: types.CallbackQuery):
    await callback.answer()
    if not shopping_list:
        await callback.message.edit_text("🛒 Список пуст.\n\n" + get_shopping_text(),
                                          reply_markup=shopping_menu_kb(), parse_mode="HTML")
        return
    await callback.message.edit_text(
        get_shopping_select_text("delete"),
        reply_markup=shopping_numbers_kb("delete"),
        parse_mode="HTML")

@dp.callback_query(F.data.startswith("delete_shopping_"))
async def shopping_delete_chosen(callback: types.CallbackQuery):
    await callback.answer("Удалено 🗑")
    sid = int(callback.data.split("_")[-1])
    if sid in shopping_list:
        del shopping_list[sid]
        await save_to_db()
    push_screen(callback.message.chat.id, "shopping")
    await callback.message.edit_text("✅ Удалено.\n\n" + get_shopping_text(),
                                      reply_markup=shopping_menu_kb(), parse_mode="HTML")

# ==================== СОБЫТИЯ ====================

@dp.callback_query(F.data == "menu_calendar")
async def show_calendar(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    push_screen(callback.message.chat.id, "calendar")
    await callback.answer()
    await callback.message.edit_text(get_calendar_text(), reply_markup=calendar_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "event_add")
async def event_add(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(EventStates.waiting_for_text)
    await callback.message.edit_text(
        "📅 <b>Новое событие</b>\n\nШаг 1/2. Введите название:",
        reply_markup=cancel_kb("menu_calendar"), parse_mode="HTML")

@dp.message(EventStates.waiting_for_text)
async def event_got_text(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if not text:
        await message.answer("⚠️ Название не может быть пустым.")
        return
    await state.update_data(text=text)
    await state.set_state(EventStates.waiting_for_date)
    await message.answer(
        "📅 Шаг 2/2. Введите дату в формате <b>ДД.ММ.ГГГГ</b>\nНапример: <code>31.12.2026</code>",
        reply_markup=cancel_kb("menu_calendar"), parse_mode="HTML")

@dp.message(EventStates.waiting_for_date)
async def event_got_date(message: types.Message, state: FSMContext):
    global event_counter
    date_str = message.text.strip()
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        await message.answer("⚠️ Неверный формат. Введите как <code>31.12.2026</code>", parse_mode="HTML")
        return
    data = await state.get_data()
    event_counter += 1
    events_data[event_counter] = {"text": data["text"], "date": date_str}
    await save_to_db()
    await state.clear()
    push_screen(message.chat.id, "calendar")
    await message.answer(f"✅ Событие добавлено: <b>{data['text']}</b> на {date_str}\n\n" + get_calendar_text(),
                         reply_markup=calendar_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "event_edit_start")
async def event_edit_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if not events_data:
        await callback.message.edit_text("📅 Нет событий для изменения.\n\n" + get_calendar_text(),
                                          reply_markup=calendar_menu_kb(), parse_mode="HTML")
        return
    await callback.message.edit_text(
        get_calendar_select_text("edit"),
        reply_markup=event_numbers_kb("edit", events_data),
        parse_mode="HTML")

@dp.callback_query(F.data.startswith("edit_event_"))
async def event_edit_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    eid = int(callback.data.split("_")[-1])
    if eid not in events_data:
        return
    e = events_data[eid]
    b = InlineKeyboardBuilder()
    b.button(text="📝 Название", callback_data=f"edit_event_text_{eid}")
    b.button(text="📅 Дату", callback_data=f"edit_event_date_{eid}")
    b.button(text="⬅️ Отмена", callback_data="menu_calendar")
    b.adjust(1, 1, 1)
    await callback.message.edit_text(
        f"✏️ <b>Изменение события</b>\n\n"
        f"Текущее: <b>{e['text']}</b> ({e['date']})\n\n"
        f"Что изменить?",
        reply_markup=b.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("edit_event_text_"))
async def event_edit_text(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    eid = int(callback.data.split("_")[-1])
    await state.update_data(edit_event_id=eid, edit_field="text")
    await state.set_state(EventStates.waiting_for_edit_value)
    await callback.message.edit_text(
        "📝 Введите новое название события:",
        reply_markup=cancel_kb("menu_calendar"), parse_mode="HTML")

@dp.callback_query(F.data.startswith("edit_event_date_"))
async def event_edit_date(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    eid = int(callback.data.split("_")[-1])
    await state.update_data(edit_event_id=eid, edit_field="date")
    await state.set_state(EventStates.waiting_for_edit_value)
    await callback.message.edit_text(
        "📅 Введите новую дату в формате <b>ДД.ММ.ГГГГ</b>:",
        reply_markup=cancel_kb("menu_calendar"), parse_mode="HTML")

@dp.message(EventStates.waiting_for_edit_value)
async def event_edit_value(message: types.Message, state: FSMContext):
    data = await state.get_data()
    eid = data["edit_event_id"]
    field = data["edit_field"]
    value = message.text.strip()
    if eid not in events_data:
        await state.clear()
        await message.answer("⚠️ Событие не найдено.", reply_markup=calendar_menu_kb(), parse_mode="HTML")
        return
    if field == "date":
        try:
            datetime.strptime(value, "%d.%m.%Y")
        except ValueError:
            await message.answer("⚠️ Неверный формат даты.", parse_mode="HTML")
            return
    events_data[eid][field] = value
    await save_to_db()
    await state.clear()
    push_screen(message.chat.id, "calendar")
    await message.answer("✅ Событие обновлено!\n\n" + get_calendar_text(),
                         reply_markup=calendar_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "event_delete_start")
async def event_delete_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if not events_data:
        await callback.message.edit_text("📅 Нет событий для удаления.\n\n" + get_calendar_text(),
                                          reply_markup=calendar_menu_kb(), parse_mode="HTML")
        return
    await callback.message.edit_text(
        get_calendar_select_text("delete"),
        reply_markup=event_numbers_kb("delete", events_data),
        parse_mode="HTML")

@dp.callback_query(F.data.startswith("delete_event_"))
async def event_delete_chosen(callback: types.CallbackQuery):
    await callback.answer("Удалено 🗑")
    eid = int(callback.data.split("_")[-1])
    if eid in events_data:
        del events_data[eid]
        await save_to_db()
    push_screen(callback.message.chat.id, "calendar")
    await callback.message.edit_text("✅ Удалено.\n\n" + get_calendar_text(),
                                      reply_markup=calendar_menu_kb(), parse_mode="HTML")

# ==================== ЗАМЕТКИ ====================

@dp.callback_query(F.data == "menu_notes")
async def show_notes(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    push_screen(callback.message.chat.id, "notes")
    await callback.answer()
    await callback.message.edit_text(get_notes_text(), reply_markup=notes_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "note_add")
async def note_add(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(NoteStates.waiting_for_title)
    await callback.message.edit_text(
        "📝 <b>Новая заметка</b>\n\nШаг 1/2. Введите заголовок:",
        reply_markup=cancel_kb("menu_notes"), parse_mode="HTML")

@dp.message(NoteStates.waiting_for_title)
async def note_got_title(message: types.Message, state: FSMContext):
    title = message.text.strip()
    if not title:
        await message.answer("⚠️ Заголовок не может быть пустым.")
        return
    await state.update_data(title=title)
    await state.set_state(NoteStates.waiting_for_text)
    await message.answer(
        "📝 Шаг 2/2. Введите текст заметки:",
        reply_markup=cancel_kb("menu_notes"), parse_mode="HTML")

@dp.message(NoteStates.waiting_for_text)
async def note_got_text(message: types.Message, state: FSMContext):
    global note_counter
    text = message.text.strip()
    if not text:
        await message.answer("⚠️ Текст не может быть пустым.")
        return
    data = await state.get_data()
    note_counter += 1
    notes_data[note_counter] = {
        "title": data["title"],
        "text": text,
        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }
    await save_to_db()
    await state.clear()
    push_screen(message.chat.id, "notes")
    await message.answer(f"✅ Заметка «{data['title']}» добавлена!\n\n" + get_notes_text(),
                         reply_markup=notes_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "note_edit_start")
async def note_edit_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if not notes_data:
        await callback.message.edit_text("📝 Нет заметок для изменения.\n\n" + get_notes_text(),
                                          reply_markup=notes_menu_kb(), parse_mode="HTML")
        return
    await callback.message.edit_text(
        get_notes_select_text("edit"),
        reply_markup=note_numbers_kb("edit", notes_data),
        parse_mode="HTML")

@dp.callback_query(F.data.startswith("edit_note_"))
async def note_edit_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    nid = int(callback.data.split("_")[-1])
    if nid not in notes_data:
        return
    n = notes_data[nid]
    b = InlineKeyboardBuilder()
    b.button(text="📝 Заголовок", callback_data=f"edit_note_title_{nid}")
    b.button(text="📄 Текст", callback_data=f"edit_note_text_{nid}")
    b.button(text="⬅️ Отмена", callback_data="menu_notes")
    b.adjust(1, 1, 1)
    await callback.message.edit_text(
        f"✏️ <b>Изменение заметки</b>\n\n"
        f"Текущий заголовок: <b>{n['title']}</b>\n\n"
        f"Что изменить?",
        reply_markup=b.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("edit_note_title_"))
async def note_edit_title(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    nid = int(callback.data.split("_")[-1])
    await state.update_data(edit_note_id=nid, edit_field="title")
    await state.set_state(NoteStates.waiting_for_edit_value)
    await callback.message.edit_text(
        "📝 Введите новый заголовок:",
        reply_markup=cancel_kb("menu_notes"), parse_mode="HTML")

@dp.callback_query(F.data.startswith("edit_note_text_"))
async def note_edit_text(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    nid = int(callback.data.split("_")[-1])
    await state.update_data(edit_note_id=nid, edit_field="text")
    await state.set_state(NoteStates.waiting_for_edit_value)
    await callback.message.edit_text(
        "📄 Введите новый текст заметки:",
        reply_markup=cancel_kb("menu_notes"), parse_mode="HTML")

@dp.message(NoteStates.waiting_for_edit_value)
async def note_edit_value(message: types.Message, state: FSMContext):
    data = await state.get_data()
    nid = data["edit_note_id"]
    field = data["edit_field"]
    value = message.text.strip()
    if nid not in notes_data:
        await state.clear()
        await message.answer("⚠️ Заметка не найдена.", reply_markup=notes_menu_kb(), parse_mode="HTML")
        return
    notes_data[nid][field] = value
    await save_to_db()
    await state.clear()
    push_screen(message.chat.id, "notes")
    await message.answer("✅ Заметка обновлена!\n\n" + get_notes_text(),
                         reply_markup=notes_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "note_delete_start")
async def note_delete_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if not notes_data:
        await callback.message.edit_text("📝 Нет заметок для удаления.\n\n" + get_notes_text(),
                                          reply_markup=notes_menu_kb(), parse_mode="HTML")
        return
    await callback.message.edit_text(
        get_notes_select_text("delete"),
        reply_markup=note_numbers_kb("delete", notes_data),
        parse_mode="HTML")

@dp.callback_query(F.data.startswith("delete_note_"))
async def note_delete_chosen(callback: types.CallbackQuery):
    await callback.answer("Удалено 🗑")
    nid = int(callback.data.split("_")[-1])
    if nid in notes_data:
        del notes_data[nid]
        await save_to_db()
    push_screen(callback.message.chat.id, "notes")
    await callback.message.edit_text("✅ Удалено.\n\n" + get_notes_text(),
                                      reply_markup=notes_menu_kb(), parse_mode="HTML")

# ==================== ОТСЧЁТ ====================

@dp.callback_query(F.data == "menu_countdown")
async def show_countdown(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    push_screen(callback.message.chat.id, "countdown")
    await callback.answer()
    await callback.message.edit_text(get_countdown_text(), reply_markup=countdown_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "countdown_add")
async def countdown_add(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(CountdownStates.waiting_for_date)
    await callback.message.edit_text(
        "⏳ <b>Новый отсчёт</b>\n\nШаг 1/2. Введите дату в формате <b>ДД.ММ.ГГГГ</b>\nНапример: <code>31.12.2026</code>",
        reply_markup=cancel_kb("menu_countdown"), parse_mode="HTML")

@dp.message(CountdownStates.waiting_for_date)
async def countdown_got_date(message: types.Message, state: FSMContext):
    date_str = message.text.strip()
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        await message.answer("⚠️ Неверный формат. Введите как <code>31.12.2026</code>", parse_mode="HTML")
        return
    await state.update_data(date=date_str)
    await state.set_state(CountdownStates.waiting_for_name)
    await message.answer(
        "⏳ Шаг 2/2. Введите название события:",
        reply_markup=cancel_kb("menu_countdown"), parse_mode="HTML")

@dp.message(CountdownStates.waiting_for_name)
async def countdown_got_name(message: types.Message, state: FSMContext):
    global event_counter
    name = message.text.strip()
    if not name:
        await message.answer("⚠️ Название не может быть пустым.")
        return
    data = await state.get_data()
    event_counter += 1
    events_data[event_counter] = {"text": name, "date": data["date"]}
    await save_to_db()
    await state.clear()
    push_screen(message.chat.id, "countdown")
    await message.answer(f"✅ Отсчёт «{name}» создан!\n\n" + get_countdown_text(),
                         reply_markup=countdown_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("view_countdown_"))
async def view_countdown(callback: types.CallbackQuery):
    await callback.answer()
    cid = int(callback.data.split("_")[-1])
    if cid in events_data:
        c = events_data[cid]
        try:
            target = datetime.strptime(c["date"], "%d.%m.%Y").date()
            days_left = (target - date.today()).days
            await callback.message.answer(
                f"⏳ <b>{c['text']}</b>\n\nДо события: <b>{days_left}</b> дней\nДата: {c['date']}",
                parse_mode="HTML")
        except Exception:
            await callback.message.answer("⚠️ Ошибка в дате события.")

# ==================== УВЕДОМЛЕНИЯ ====================

@dp.callback_query(F.data == "menu_notif")
async def show_notif(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    push_screen(callback.message.chat.id, "notif")
    await callback.answer()
    await callback.message.edit_text(get_notif_text(), reply_markup=notif_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "notif_category_once")
async def show_notif_once(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(get_notif_once_text(), reply_markup=notif_once_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "notif_category_recurring")
async def show_notif_recurring(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(get_notif_recurring_text(), reply_markup=notif_recurring_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "notif_add")
async def notif_add_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(category="once")
    await state.set_state(NotifStates.waiting_for_text)
    await callback.message.edit_text(
        "📌 <b>Разовое уведомление</b>\n\nШаг 1/3. Введите текст:",
        reply_markup=cancel_kb("notif_category_once"), parse_mode="HTML")

@dp.message(NotifStates.waiting_for_text)
async def notif_got_text(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if not text:
        await message.answer("⚠️ Текст не может быть пустым.")
        return
    await state.update_data(text=text)
    await message.answer("🔔 Шаг 2/3. <b>Выберите дату</b>:", reply_markup=notif_date_kb(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("nsetdate_"))
async def notif_set_date(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.replace("nsetdate_", "")
    if value == "custom":
        await state.set_state(NotifStates.waiting_for_custom_date)
        await callback.message.edit_text(
            "📅 Введите дату в формате <b>ДД.ММ.ГГГГ</b>\nНапример: <code>31.12.2026</code>",
            reply_markup=cancel_kb("notif_category_once"), parse_mode="HTML")
        return
    await state.update_data(date=value)
    await state.set_state(NotifStates.waiting_for_time)
    now = datetime.now().strftime("%H:%M")
    await callback.message.edit_text(
        f"📅 Дата: <b>{value}</b>\n🕐 Сейчас: <b>{now}</b>\n\n🔔 Шаг 3/3. Введите <b>время</b> ЧЧ:ММ\nНапример: <code>21:00</code>",
        reply_markup=cancel_kb("notif_category_once"), parse_mode="HTML")

@dp.message(NotifStates.waiting_for_custom_date)
async def notif_got_custom_date(message: types.Message, state: FSMContext):
    date_str = message.text.strip()
    try:
        dt = datetime.strptime(date_str, "%d.%m.%Y")
        if dt.date() < datetime.now().date():
            await message.answer("⚠️ Дата уже прошла.")
            return
    except ValueError:
        await message.answer("⚠️ Неверный формат.", parse_mode="HTML")
        return
    await state.update_data(date=date_str)
    await state.set_state(NotifStates.waiting_for_time)
    await message.answer(
        f"📅 Дата: <b>{date_str}</b>\n\n🔔 Шаг 3/3. Введите <b>время</b> ЧЧ:ММ:",
        reply_markup=cancel_kb("notif_category_once"), parse_mode="HTML")

@dp.message(NotifStates.waiting_for_time)
async def notif_got_time(message: types.Message, state: FSMContext):
    global notif_counter
    time_str = message.text.strip()
    try:
        hh, mm = map(int, time_str.split(":"))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Неверный формат. Введите как <code>21:00</code>", parse_mode="HTML")
        return
    data = await state.get_data()
    notif_counter += 1
    notifications[notif_counter] = {
        "text": data["text"], "date": data["date"],
        "time": f"{hh:02d}:{mm:02d}", "chat_id": message.chat.id, "sent": False,
        "category": "once"
    }
    await save_to_db()
    await state.clear()
    push_screen(message.chat.id, "notif")
    await message.answer(
        f"✅ Разовое уведомление создано!\n\n📅 <b>{data['date']}</b> в <b>{hh:02d}:{mm:02d}</b>\n📝 {data['text']}",
        reply_markup=notif_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "recurring_add")
async def recurring_add(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(RecurringStates.waiting_for_text)
    await callback.message.edit_text(
        "🔁 <b>Повторяющееся уведомление</b>\n\nШаг 1/3. Введите текст:",
        reply_markup=cancel_kb("notif_category_recurring"), parse_mode="HTML")

@dp.message(RecurringStates.waiting_for_text)
async def recurring_got_text(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if not text:
        await message.answer("⚠️ Текст не может быть пустым.")
        return
    await state.update_data(text=text)
    await state.set_state(RecurringStates.waiting_for_interval)
    await message.answer("🔁 Шаг 2/3. <b>Выберите периодичность:</b>", reply_markup=recurring_interval_kb(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("rec_"))
async def recurring_set_interval(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.replace("rec_", "")
    if value == "daily":
        await state.update_data(interval="daily", day=0)
        await state.set_state(RecurringStates.waiting_for_time)
        await callback.message.edit_text(
            "🔁 Каждый день\n\nШаг 3/3. Введите <b>время</b> ЧЧ:ММ\nНапример: <code>21:00</code>",
            reply_markup=cancel_kb("notif_category_recurring"), parse_mode="HTML")
    elif value == "weekly":
        await state.update_data(interval="weekly")
        await callback.message.edit_text(
            "🔁 Каждую неделю\n\n<b>Выберите день недели:</b>",
            reply_markup=recurring_weekday_kb(), parse_mode="HTML")
    elif value == "monthly":
        await state.update_data(interval="monthly")
        await state.set_state(RecurringStates.waiting_for_day)
        await callback.message.edit_text(
            "🔁 Каждый месяц\n\nВведите <b>число месяца</b> (1-31)\nНапример: <code>1</code>",
            reply_markup=cancel_kb("notif_category_recurring"), parse_mode="HTML")

@dp.callback_query(F.data.startswith("rec_day_"))
async def recurring_set_weekday(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    day_idx = int(callback.data.split("_")[-1])
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    await state.update_data(day=day_idx)
    await state.set_state(RecurringStates.waiting_for_time)
    await callback.message.edit_text(
        f"🔁 Каждую <b>{weekdays[day_idx]}</b>\n\nВведите <b>время</b> ЧЧ:ММ\nНапример: <code>10:00</code>",
        reply_markup=cancel_kb("notif_category_recurring"), parse_mode="HTML")

@dp.message(RecurringStates.waiting_for_day)
async def recurring_got_day(message: types.Message, state: FSMContext):
    try:
        day = int(message.text.strip())
        if not (1 <= day <= 31):
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите число от 1 до 31.", parse_mode="HTML")
        return
    await state.update_data(day=day)
    await state.set_state(RecurringStates.waiting_for_time)
    await message.answer(
        f"🔁 Число: <b>{day}</b>\n\nВведите <b>время</b> ЧЧ:ММ:",
        reply_markup=cancel_kb("notif_category_recurring"), parse_mode="HTML")

@dp.message(RecurringStates.waiting_for_time)
async def recurring_got_time(message: types.Message, state: FSMContext):
    global recurring_counter
    time_str = message.text.strip()
    try:
        hh, mm = map(int, time_str.split(":"))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Неверный формат.", parse_mode="HTML")
        return
    data = await state.get_data()
    recurring_counter += 1
    recurring_payments[recurring_counter] = {
        "text": data["text"],
        "interval": data.get("interval", "monthly"),
        "day": data.get("day", 0),
        "time": f"{hh:02d}:{mm:02d}",
        "chat_id": message.chat.id,
        "last_sent": ""
    }
    await save_to_db()
    await state.clear()
    push_screen(message.chat.id, "notif")
    await message.answer(
        f"✅ Повторяющееся уведомление создано!\n\n🔁 <b>{data.get('interval')}</b> в <b>{hh:02d}:{mm:02d}</b>\n📝 {data['text']}",
        reply_markup=notif_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("del_recurring_"))
async def recurring_delete(callback: types.CallbackQuery):
    await callback.answer("Удалено 🗑")
    rid = int(callback.data.split("_")[-1])
    if rid in recurring_payments:
        del recurring_payments[rid]
        await save_to_db()
    await callback.message.edit_text(get_notif_recurring_text(), reply_markup=notif_recurring_kb(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("del_notif_"))
async def delete_notif(callback: types.CallbackQuery):
    await callback.answer("Удалено 🗑")
    nid = int(callback.data.split("_")[-1])
    if nid in notifications:
        del notifications[nid]
        await save_to_db()
    await callback.message.edit_text(get_notif_once_text(), reply_markup=notif_once_kb(), parse_mode="HTML")

# ==================== ФИНАНСЫ ====================

@dp.callback_query(F.data == "menu_finance")
async def show_finance(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    push_screen(callback.message.chat.id, "finance")
    await callback.answer()
    await callback.message.edit_text(get_finance_text(), reply_markup=finance_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("fin_player_"))
async def show_player_stats(callback: types.CallbackQuery):
    await callback.answer()
    pid = int(callback.data.split("_")[-1])
    push_screen(callback.message.chat.id, f"player_{pid}")
    await callback.message.edit_text(get_player_text(pid), reply_markup=player_actions_kb(pid), parse_mode="HTML")

@dp.callback_query(F.data.startswith("add_"))
async def ask_amount_add(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    pid = int(callback.data.split("_")[-1])
    await state.update_data(player_id=pid, action="add")
    await state.set_state(MoneyStates.waiting_for_amount)
    player = users_data[pid]
    await callback.message.edit_text(
        f"➕ <b>Внести</b> для {player['name']}\n\n"
        f"💵 Текущий баланс: <b>{player['balance']}</b>\n\n"
        f"Введите сумму:",
        reply_markup=cancel_kb("menu_finance"), parse_mode="HTML")

@dp.callback_query(F.data.startswith("sub_"))
async def ask_amount_sub(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    pid = int(callback.data.split("_")[-1])
    await state.update_data(player_id=pid, action="sub")
    await state.set_state(MoneyStates.waiting_for_amount)
    player = users_data[pid]
    await callback.message.edit_text(
        f"➖ <b>Убрать</b> у {player['name']}\n\n"
        f"💵 Текущий баланс: <b>{player['balance']}</b>\n\n"
        f"Введите сумму:",
        reply_markup=cancel_kb("menu_finance"), parse_mode="HTML")

@dp.message(MoneyStates.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите положительное число. Например: <code>500</code>", parse_mode="HTML")
        return
    data = await state.get_data()
    pid = data["player_id"]
    action = data["action"]
    player = users_data[pid]
    now = datetime.now()
    if action == "add":
        player["balance"] += amount
        sign = "+"
    else:
        player["balance"] -= amount
        sign = "-"

    player["history"].append({
        "date": now, "amount": amount, "sign": sign,
        "player": player["name"], "player_id": pid,
    })
    balance_history.append((now, users_data[1]["balance"] + users_data[2]["balance"]))
    await save_to_db()
    push_screen(message.chat.id, f"player_{pid}")
    await state.clear()
    await message.answer(
        f"✅ Готово! {player['name']}: {sign}{amount}\n\n" + get_player_text(pid),
        reply_markup=player_actions_kb(pid), parse_mode="HTML")

# ==================== ИСТОРИЯ ====================

@dp.callback_query(F.data == "hist_open")
async def hist_open(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    push_screen(callback.message.chat.id, "history")
    await callback.answer()
    await state.update_data(filter_type="all", player_filter="all", date_filter="all",
                            min_amount=None, page=1)
    await render_history(callback, state)

async def render_history(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    records = get_all_records()
    records = filter_records(
        records,
        filter_type=data.get("filter_type", "all"),
        date_filter=data.get("date_filter", "all"),
        player_filter=data.get("player_filter", "all"),
        min_amount=data.get("min_amount"),
    )
    page = data.get("page", 1)
    text, page, total_pages = format_records_page(records, page)
    await state.update_data(page=page)
    await callback.message.edit_text(
        text, reply_markup=history_filters_kb(data, page, total_pages), parse_mode="HTML")

@dp.callback_query(F.data.startswith("hist_page_"))
async def hist_page(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    page = int(callback.data.split("_")[-1])
    await state.update_data(page=page)
    await render_history(callback, state)

@dp.callback_query(F.data == "hist_noop")
async def hist_noop(callback: types.CallbackQuery):
    await callback.answer()

@dp.callback_query(F.data.startswith("hist_set_"))
async def hist_set_type(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.replace("hist_set_", "")
    await state.update_data(filter_type=value, page=1)
    await render_history(callback, state)

@dp.callback_query(F.data.startswith("hist_player_"))
async def hist_set_player(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.replace("hist_player_", "")
    await state.update_data(player_filter=value, page=1)
    await render_history(callback, state)

@dp.callback_query(F.data.startswith("hist_date_"))
async def hist_set_date(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.replace("hist_date_", "")
    await state.update_data(date_filter=value, page=1)
    await render_history(callback, state)

@dp.callback_query(F.data == "hist_reset")
async def hist_reset(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Фильтры сброшены")
    await state.update_data(filter_type="all", player_filter="all", date_filter="all",
                            min_amount=None, page=1)
    await render_history(callback, state)

@dp.callback_query(F.data == "hist_search_amount")
async def hist_search_amount(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(HistoryStates.waiting_for_min_amount)
    await callback.message.edit_text(
        "🔍 <b>Поиск по сумме</b>\n\nВведите минимальную сумму:",
        reply_markup=cancel_kb("hist_open"), parse_mode="HTML")

@dp.message(HistoryStates.waiting_for_min_amount)
async def hist_got_min_amount(message: types.Message, state: FSMContext):
    try:
        min_amt = int(message.text.strip())
        if min_amt < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите положительное число.", parse_mode="HTML")
        return
    await state.update_data(min_amount=min_amt, page=1)
    await state.set_state(None)
    data = await state.get_data()
    records = get_all_records()
    records = filter_records(
        records,
        filter_type=data.get("filter_type", "all"),
        date_filter=data.get("date_filter", "all"),
        player_filter=data.get("player_filter", "all"),
        min_amount=min_amt,
    )
    text, page, total_pages = format_records_page(records, 1)
    await state.update_data(page=page)
    await message.answer(
        text, reply_markup=history_filters_kb(data, page, total_pages), parse_mode="HTML")

@dp.callback_query(F.data == "hist_export")
async def hist_export(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Готовлю файл...")
    data = await state.get_data()
    records = get_all_records()
    records = filter_records(
        records,
        filter_type=data.get("filter_type", "all"),
        date_filter=data.get("date_filter", "all"),
        player_filter=data.get("player_filter", "all"),
        min_amount=data.get("min_amount"),
    )
    if not records:
        await callback.message.answer("📥 Нечего экспортировать.")
        return
    buf = make_history_csv(records)
    await callback.message.answer_document(
        document=types.BufferedInputFile(buf.read(), filename="history.csv"),
        caption=f"📥 Экспорт истории ({len(records)} записей)")

# ==================== ДАННЫЕ ====================

@dp.callback_query(F.data == "menu_data")
async def show_data(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    push_screen(callback.message.chat.id, "data")
    await callback.answer()
    await callback.message.edit_text(get_data_text(), reply_markup=data_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "chart_balances")
async def chart_balances(callback: types.CallbackQuery):
    await callback.answer("Строю график...")
    buf = make_balances_chart()
    await callback.message.answer_photo(
        photo=types.BufferedInputFile(buf.read(), filename="balances.png"),
        caption="📊 <b>Текущие балансы игроков</b>", parse_mode="HTML")

@dp.callback_query(F.data == "chart_history")
async def chart_history(callback: types.CallbackQuery):
    await callback.answer("Строю график...")
    if len(balance_history) < 2:
        await callback.message.answer("📈 Пока недостаточно данных.")
        return
    buf = make_history_chart()
    await callback.message.answer_photo(
        photo=types.BufferedInputFile(buf.read(), filename="history.png"),
        caption="📈 <b>Динамика общего баланса</b>", parse_mode="HTML")

# ==================== ПЛАНИРОВЩИК ====================

async def notif_scheduler():
    while True:
        now = datetime.now()
        today = now.strftime("%d.%m.%Y")
        current_time = now.strftime("%H:%M")
        weekday = now.weekday()
        day_of_month = now.day
        changed = False

        for nid, n in list(notifications.items()):
            if n.get("sent"):
                continue
            if n["date"] == today and n["time"] <= current_time:
                try:
                    await bot.send_message(
                        chat_id=n["chat_id"],
                        text=f"🔔 <b>Напоминание</b>\n\n{n['text']}",
                        parse_mode="HTML")
                    n["sent"] = True
                    del notifications[nid]
                    changed = True
                except Exception as e:
                    logging.error(f"Ошибка отправки {nid}: {e}")

        for rid, r in list(recurring_payments.items()):
            if r["time"] > current_time:
                continue
            interval = r.get("interval", "monthly")
            should_send = False
            if interval == "daily":
                should_send = r.get("last_sent") != today
            elif interval == "weekly":
                if r.get("day", 0) == weekday:
                    should_send = r.get("last_sent") != today
            elif interval == "monthly":
                if r.get("day", 0) == day_of_month:
                    should_send = r.get("last_sent") != today

            if should_send:
                try:
                    await bot.send_message(
                        chat_id=r["chat_id"],
                        text=f"🔁 <b>Напоминание</b>\n\n{r['text']}",
                        parse_mode="HTML")
                    r["last_sent"] = today
                    changed = True
                except Exception as e:
                    logging.error(f"Ошибка отправки {rid}: {e}")

        if changed:
            await save_to_db()
        await asyncio.sleep(30)

# ==================== ЗАПУСК ====================

async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот запущен...")

    await init_db()
    await load_from_db()

    asyncio.create_task(notif_scheduler())
    asyncio.create_task(autosave_loop())

    try:
        await dp.start_polling(bot)
    finally:
        await save_to_db()
        make_checkpoint(f"shutdown_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        print("💾 Данные сохранены при выходе")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")