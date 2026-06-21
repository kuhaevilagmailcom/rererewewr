import calendar
import html
import os
import re
import sqlite3

import httpx
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "team.db"
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0") or "0")
MANAGER_IDS = {
    int(x.strip())
    for x in os.getenv("MANAGER_IDS", os.getenv("ROLE_MANAGER_IDS", "")).split(",")
    if x.strip().isdigit()
}

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
PROJECT_START_DATE_RAW = os.getenv("PROJECT_START_DATE", "2026-05-10").strip()

# OpenModel / DeepSeek AI answers in private messages only.
# Get the key in OpenModel Console and put it into .env as OPENMODEL_API_KEY.
OPENMODEL_API_KEY = os.getenv("OPENMODEL_API_KEY", "").strip()
OPENMODEL_BASE_URL = os.getenv("OPENMODEL_BASE_URL", "https://api.openmodel.ai/v1").strip().rstrip("/")
OPENMODEL_MODEL = os.getenv("OPENMODEL_MODEL", "deepseek-v4-flash").strip()
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "900") or "900")
AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", "0.7") or "0.7")
AI_HISTORY_LIMIT = int(os.getenv("AI_HISTORY_LIMIT", "12") or "12")
AI_SYSTEM_PROMPT = os.getenv(
    "AI_SYSTEM_PROMPT",
    "Ты Telegram-помощник проекта Elite Russia. "
    "Elite Russia — CRMP / GTA SA / SA:MP проект. "
    "Отвечай по-русски, кратко, понятно и по делу. "
    "Не обещай точную дату открытия: дата открытия пока неизвестна. "
    "Если спрашивают про вступление в команду — направляй на сайт elite-crmp.ru/#team. "
    "Если предлагают идею — направляй на elite-crmp.ru/#ideas. "
    "Если спрашивают канал — t.me/eliterussian."
).strip()

MANAGER_ROLES = {
    "owner", "владелец", "создатель", "руководитель", "lead", "тимлид",
    "admin", "админ", "администратор", "основатель",
}

CHANNEL_URL = "https://t.me/eliterussian"
TEAM_URL = "https://elite-crmp.ru/#team"
IDEAS_URL = "https://elite-crmp.ru/#ideas"


# -------------------- DB --------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def init_db():
    """Аккуратная миграция: таблицы/колонки добавляем, users не пересоздаём."""
    with db() as conn:
        users_existed = table_exists(conn, "users")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                key TEXT PRIMARY KEY,
                tg_id INTEGER UNIQUE,
                username TEXT,
                display_name TEXT NOT NULL,
                role TEXT DEFAULT 'Сотрудник',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for col, ddl in [
            ("tg_id", "ALTER TABLE users ADD COLUMN tg_id INTEGER"),
            ("username", "ALTER TABLE users ADD COLUMN username TEXT"),
            ("display_name", "ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT 'Пользователь'"),
            ("role", "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'Сотрудник'"),
            ("created_at", "ALTER TABLE users ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP"),
            ("updated_at", "ALTER TABLE users ADD COLUMN updated_at TEXT DEFAULT CURRENT_TIMESTAMP"),
        ]:
            if not column_exists(conn, "users", col):
                conn.execute(ddl)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS message_stats (
                stat_date TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                display_name TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (stat_date, chat_id, user_id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER,
                actor_name TEXT,
                action TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_private_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT,
                assignee_key TEXT,
                assignee_name TEXT,
                assignee_tg_id INTEGER,
                creator_id INTEGER,
                creator_name TEXT,
                status TEXT DEFAULT 'В работе',
                admin_message_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                done_at TEXT
            )
            """
        )
        for col, ddl in [
            ("text", "ALTER TABLE tasks ADD COLUMN text TEXT"),
            ("assignee_tg_id", "ALTER TABLE tasks ADD COLUMN assignee_tg_id INTEGER"),
            ("creator_id", "ALTER TABLE tasks ADD COLUMN creator_id INTEGER"),
            ("creator_name", "ALTER TABLE tasks ADD COLUMN creator_name TEXT"),
            ("admin_message_id", "ALTER TABLE tasks ADD COLUMN admin_message_id INTEGER"),
            ("done_at", "ALTER TABLE tasks ADD COLUMN done_at TEXT"),
        ]:
            if not column_exists(conn, "tasks", col):
                conn.execute(ddl)
        if column_exists(conn, "tasks", "title"):
            conn.execute("UPDATE tasks SET text=title WHERE (text IS NULL OR text='') AND title IS NOT NULL")
        if column_exists(conn, "tasks", "created_by_id"):
            conn.execute("UPDATE tasks SET creator_id=created_by_id WHERE creator_id IS NULL AND created_by_id IS NOT NULL")
        if column_exists(conn, "tasks", "created_by_name"):
            conn.execute("UPDATE tasks SET creator_name=created_by_name WHERE (creator_name IS NULL OR creator_name='') AND created_by_name IS NOT NULL")

        # Старую team_roles мигрируем только если users пустая/новая.
        # Иначе удалённые через /removerole люди могли бы вернуться после перезапуска.
        users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if table_exists(conn, "team_roles") and (not users_existed or users_count == 0):
            for r in conn.execute("SELECT * FROM team_roles").fetchall():
                key = r["target_key"]
                display_name = r["display_name"] or "Пользователь"
                role = r["role_text"] or "Сотрудник"
                tg_id = None
                username = None
                if key.startswith("id:") and key[3:].isdigit():
                    tg_id = int(key[3:])
                elif key.startswith("username:"):
                    username = key.split(":", 1)[1].lower()
                conn.execute(
                    "INSERT OR IGNORE INTO users(key, tg_id, username, display_name, role) VALUES(?,?,?,?,?)",
                    (key, tg_id, username, display_name, role),
                )

        dedupe_users(conn)
        conn.commit()


def dedupe_users(conn: sqlite3.Connection):
    """Убирает дубли с одинаковым username, не удаляя роли у основной записи."""
    rows = conn.execute(
        "SELECT * FROM users WHERE username IS NOT NULL AND username != '' ORDER BY tg_id IS NULL, updated_at DESC"
    ).fetchall()
    by_username: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_username.setdefault(row["username"].lower(), []).append(row)

    for _uname, items in by_username.items():
        if len(items) <= 1:
            continue
        primary = next((r for r in items if r["tg_id"] is not None), items[0])
        for item in items:
            if item["key"] == primary["key"]:
                continue
            if (not primary["role"] or primary["role"] == "Сотрудник") and item["role"]:
                conn.execute("UPDATE users SET role=? WHERE key=?", (item["role"], primary["key"]))
            conn.execute("UPDATE tasks SET assignee_key=? WHERE assignee_key=?", (primary["key"], item["key"]))
            conn.execute("DELETE FROM users WHERE key=?", (item["key"],))


# -------------------- HELPERS --------------------

def plural_ru(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(n) % 100
    n1 = n_abs % 10
    if 11 <= n_abs <= 19:
        return many
    if n1 == 1:
        return one
    if 2 <= n1 <= 4:
        return few
    return many


def parse_project_start_date() -> date:
    try:
        return datetime.strptime(PROJECT_START_DATE_RAW, "%Y-%m-%d").date()
    except ValueError:
        return date(2026, 5, 10)


def add_months(src: date, months: int) -> date:
    month = src.month - 1 + months
    year = src.year + month // 12
    month = month % 12 + 1
    day = min(src.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def project_duration_text(today: Optional[date] = None) -> str:
    today = today or datetime.now(TIMEZONE).date()
    start = parse_project_start_date()
    if today < start:
        return "0 месяцев 0 дней"
    months = (today.year - start.year) * 12 + (today.month - start.month)
    if today.day < start.day:
        months -= 1
    anchor = add_months(start, max(months, 0))
    days = (today - anchor).days
    return f"{months} {plural_ru(months, 'месяц', 'месяца', 'месяцев')} {days} {plural_ru(days, 'день', 'дня', 'дней')}"


def user_key_from_tg(tg_id: int) -> str:
    return f"id:{tg_id}"


def user_key_from_username(username: str) -> str:
    return f"username:{username.strip().lstrip('@').lower()}"


def clean_name(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    name = " ".join(filter(None, [getattr(user, "first_name", ""), getattr(user, "last_name", "")])).strip()
    return name or str(user.id)


def html_user_link(tg_id: Optional[int], display_name: str, username: Optional[str] = None) -> str:
    if username:
        safe_user = html.escape(username.lstrip("@"))
        return f'<a href="https://t.me/{safe_user}">@{safe_user}</a>'
    raw_name = (display_name or "").strip()
    if tg_id and raw_name == str(tg_id):
        raw_name = "Пользователь"
    safe_name = html.escape(raw_name or (str(tg_id) if tg_id else "Пользователь"))
    if tg_id:
        return f'<a href="tg://user?id={int(tg_id)}">{safe_name}</a>'
    return safe_name


def user_name_for_users_list(row) -> str:
    username = (row["username"] or "").strip().lstrip("@")
    if username:
        return f"@{html.escape(username)}"
    return html_user_link(row["tg_id"], row["display_name"], None)


def user_link_from_tg_user(user) -> str:
    username = (getattr(user, "username", None) or None)
    display = f"@{username}" if username else clean_name(user)
    return html_user_link(user.id, display, username)


def normalize_target(raw: str) -> tuple[str, str, Optional[int], Optional[str]]:
    raw = raw.strip()
    if not raw:
        raise ValueError("Укажи @username или Telegram ID")
    if raw.startswith("@"):
        username = raw[1:].strip().lower()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
            raise ValueError("Некорректный username. Пример: @username")
        return user_key_from_username(username), f"@{username}", None, username
    if raw.isdigit():
        tg_id = int(raw)
        return user_key_from_tg(tg_id), f"Пользователь", tg_id, None
    if re.fullmatch(r"[A-Za-z0-9_]{3,32}", raw):
        username = raw.lower()
        return user_key_from_username(username), f"@{username}", None, username
    raise ValueError("Укажи @username или Telegram ID")


def update_existing_user_identity(tg_user):
    """Обновляет username/name только у тех, кто уже есть в users. Новых людей НЕ создаёт."""
    if not tg_user:
        return
    username = (tg_user.username or "").lower() or None
    display = clean_name(tg_user)
    key_id = user_key_from_tg(tg_user.id)

    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_user.id,)).fetchone()
        if not row and username:
            row = conn.execute("SELECT * FROM users WHERE lower(username)=?", (username,)).fetchone()
        if not row:
            return

        old_key = row["key"]
        new_key = key_id if tg_user.id else old_key
        conn.execute(
            """
            UPDATE users
            SET key=?, tg_id=?, username=COALESCE(?, username), display_name=?, updated_at=CURRENT_TIMESTAMP
            WHERE key=?
            """,
            (new_key, tg_user.id, username, display, old_key),
        )
        if old_key != new_key:
            conn.execute("UPDATE tasks SET assignee_key=? WHERE assignee_key=?", (new_key, old_key))
        conn.commit()


def get_user_by_tg_id(tg_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()


def is_manager(tg_id: int) -> bool:
    if tg_id in MANAGER_IDS:
        return True
    row = get_user_by_tg_id(tg_id)
    if not row:
        return False
    return (row["role"] or "").strip().lower() in MANAGER_ROLES


def log_action(actor_id: int, actor_name: str, action: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO logs(actor_id, actor_name, action) VALUES(?,?,?)",
            (actor_id, actor_name, action),
        )
        conn.commit()


# -------------------- ACTIVITY --------------------

def today_iso() -> str:
    return datetime.now(TIMEZONE).date().isoformat()


def activity_status(count: int) -> str:
    if count <= 0:
        return "💤 AFK"
    if count < 10:
        return "🤐 Молчун"
    return "💬 Болтун"


def get_today_count_for_user(conn: sqlite3.Connection, row) -> int:
    if not ADMIN_GROUP_ID:
        return 0
    stat_date = today_iso()
    total = 0
    seen_ids = set()

    if row["tg_id"]:
        r = conn.execute(
            "SELECT count FROM message_stats WHERE stat_date=? AND chat_id=? AND user_id=?",
            (stat_date, ADMIN_GROUP_ID, row["tg_id"]),
        ).fetchone()
        if r:
            total += int(r["count"])
            seen_ids.add(row["tg_id"])

    username = (row["username"] or "").lower()
    if username:
        rows = conn.execute(
            "SELECT user_id, count FROM message_stats WHERE stat_date=? AND chat_id=? AND lower(username)=?",
            (stat_date, ADMIN_GROUP_ID, username),
        ).fetchall()
        for r in rows:
            if r["user_id"] not in seen_ids:
                total += int(r["count"])
                seen_ids.add(r["user_id"])
    return total


def get_all_team_users_with_counts():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM users
            WHERE role IS NOT NULL AND trim(role) != ''
            ORDER BY
                CASE
                    WHEN role IN ('Владелец','Создатель','Основатель') OR lower(role) IN ('owner','creator','founder') THEN 0
                    WHEN role IN ('Руководитель','Тимлид','Админ','Администратор') OR lower(role) IN ('lead','admin') THEN 1
                    ELSE 2
                END,
                display_name
            """
        ).fetchall()
        result = []
        for r in rows:
            count = get_today_count_for_user(conn, r)
            result.append((r, count))
        return result


# -------------------- COMMANDS --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_existing_user_identity(update.effective_user)

    if update.effective_chat and update.effective_chat.type != "private":
        await update.message.reply_text("Напиши боту в ЛС — там будет информация по Elite Russia.")
        return

    start_date = parse_project_start_date().strftime("%d.%m.%Y")
    text = (
        "👋 <b>Привет!</b>\n\n"
        "Elite Russia сейчас находится в разработке.\n\n"
        f"📅 Разработка началась: <b>{start_date}</b>\n"
        f"🚧 Разработка идёт уже: <b>{project_duration_text()}</b>\n"
        "⏳ До открытия: <b>пока неизвестно</b>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Наш Telegram канал", url=CHANNEL_URL)],
        [InlineKeyboardButton("🧠 Подать заявку", url=TEAM_URL)],
        [InlineKeyboardButton("💡 Предложить идею", url=IDEAS_URL)],
        [InlineKeyboardButton("📅 Когда открытие?", callback_data="info:opening")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_existing_user_identity(update.effective_user)
    await update.message.reply_text("Панель убрана. Команда проекта показывается через /users.")


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_existing_user_identity(update.effective_user)
    if not is_manager(update.effective_user.id):
        await update.message.reply_text("Нет доступа.")
        return
    await update.message.reply_text(format_users(), parse_mode="HTML", disable_web_page_preview=True)


def format_users() -> str:
    rows = get_all_team_users_with_counts()
    if not rows:
        return "👥 <b>Команда проекта пока пустая.</b>"

    lines = ["👥 <b>Команда проекта Elite Russia</b>", ""]
    for r, count in rows:
        lines.append(f"— {user_name_for_users_list(r)} — {html.escape(r['role'])} {activity_status(count)}")
    return "\n".join(lines)


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_existing_user_identity(update.effective_user)
    if not is_manager(update.effective_user.id):
        await update.message.reply_text("Нет доступа.")
        return
    await update.message.reply_text(format_top(), parse_mode="HTML", disable_web_page_preview=True)


def format_top(report_date: Optional[str] = None) -> str:
    report_date = report_date or today_iso()
    rows = []
    with db() as conn:
        team = conn.execute(
            "SELECT * FROM users WHERE role IS NOT NULL AND trim(role) != ''"
        ).fetchall()
        for r in team:
            count = 0
            seen_ids = set()
            if ADMIN_GROUP_ID and r["tg_id"]:
                stat = conn.execute(
                    "SELECT count FROM message_stats WHERE stat_date=? AND chat_id=? AND user_id=?",
                    (report_date, ADMIN_GROUP_ID, r["tg_id"]),
                ).fetchone()
                if stat:
                    count += int(stat["count"])
                    seen_ids.add(r["tg_id"])
            username = (r["username"] or "").lower()
            if ADMIN_GROUP_ID and username:
                stats = conn.execute(
                    "SELECT user_id, count FROM message_stats WHERE stat_date=? AND chat_id=? AND lower(username)=?",
                    (report_date, ADMIN_GROUP_ID, username),
                ).fetchall()
                for stat in stats:
                    if stat["user_id"] not in seen_ids:
                        count += int(stat["count"])
                        seen_ids.add(stat["user_id"])
            rows.append((r, count))

    rows.sort(key=lambda x: (-x[1], (x[0]["display_name"] or "").lower()))

    lines = ["🏆 <b>Топ активности Elite Russia</b>", f"Дата: <b>{html.escape(report_date)}</b>", ""]
    if not rows:
        lines.append("Команда проекта пока пустая.")
    else:
        total = sum(c for _, c in rows)
        for i, (r, count) in enumerate(rows, start=1):
            lines.append(
                f"{i}. {user_name_for_users_list(r)} — <b>{count}</b> "
                f"{plural_ru(count, 'сообщение', 'сообщения', 'сообщений')} {activity_status(count)}"
            )
        lines.append("")
        lines.append(f"Всего сообщений команды: <b>{total}</b>")
    lines.append("")
    lines.append(f"🚧 Разработка идёт: <b>{project_duration_text()}</b>")
    return "\n".join(lines)


async def setrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_existing_user_identity(update.effective_user)
    if not is_manager(update.effective_user.id):
        await update.message.reply_text("Нет доступа.")
        return

    reply_user = update.message.reply_to_message.from_user if update.message.reply_to_message else None

    if reply_user:
        if len(context.args) < 1:
            await update.message.reply_text("Ответь на сообщение человека так: /setrole Разработчик")
            return
        key = user_key_from_tg(reply_user.id)
        username = (reply_user.username or "").lower() or None
        display = clean_name(reply_user)
        tg_id = reply_user.id
        role = " ".join(context.args).strip()
    else:
        if len(context.args) < 2:
            await update.message.reply_text(
                "Формат:\n"
                "<code>/setrole @username роль</code>\n"
                "<code>/setrole 123456789 роль</code>\n\n"
                "Если у человека нет username — ответь на его сообщение: <code>/setrole роль</code>",
                parse_mode="HTML",
            )
            return
        try:
            key, display, tg_id, username = normalize_target(context.args[0])
        except ValueError as e:
            await update.message.reply_text(str(e))
            return
        role = " ".join(context.args[1:]).strip()

    if len(role) < 2 or len(role) > 80:
        await update.message.reply_text("Название роли должно быть от 2 до 80 символов.")
        return

    with db() as conn:
        existing = None
        if tg_id:
            existing = conn.execute("SELECT * FROM users WHERE tg_id=? OR key=?", (tg_id, key)).fetchone()
        elif username:
            existing = conn.execute("SELECT * FROM users WHERE lower(username)=? OR key=?", (username, key)).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE users
                SET tg_id=COALESCE(?, tg_id), username=COALESCE(?, username), display_name=?, role=?, updated_at=CURRENT_TIMESTAMP
                WHERE key=?
                """,
                (tg_id, username, display, role, existing["key"]),
            )
        else:
            conn.execute(
                "INSERT INTO users(key, tg_id, username, display_name, role) VALUES(?,?,?,?,?)",
                (key, tg_id, username, display, role),
            )
        dedupe_users(conn)
        conn.commit()

    log_action(update.effective_user.id, clean_name(update.effective_user), f"setrole {display} -> {role}")
    await update.message.reply_text(
        f"✅ Роль выдана\n\n{html_user_link(tg_id, display, username)} — <b>{html.escape(role)}</b>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def removerole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_existing_user_identity(update.effective_user)
    if not is_manager(update.effective_user.id):
        await update.message.reply_text("Нет доступа.")
        return

    reply_user = update.message.reply_to_message.from_user if update.message.reply_to_message else None
    rows = []

    with db() as conn:
        if reply_user:
            keys = [user_key_from_tg(reply_user.id)]
            if reply_user.username:
                keys.append(user_key_from_username(reply_user.username))
            placeholders = ",".join("?" for _ in keys)
            rows.extend(conn.execute(f"SELECT * FROM users WHERE key IN ({placeholders}) OR tg_id=?", (*keys, reply_user.id)).fetchall())
        else:
            if len(context.args) < 1:
                await update.message.reply_text(
                    "Формат:\n"
                    "<code>/removerole @username</code>\n"
                    "<code>/removerole username</code>\n"
                    "<code>/removerole 123456789</code>\n\n"
                    "Можно ответить на сообщение человека: <code>/removerole</code>",
                    parse_mode="HTML",
                )
                return
            raw = context.args[0].strip()
            if raw.startswith("@") or re.fullmatch(r"[A-Za-z0-9_]{3,32}", raw):
                username = raw.lstrip("@").lower()
                rows.extend(conn.execute("SELECT * FROM users WHERE lower(username)=? OR key=?", (username, user_key_from_username(username))).fetchall())
            elif raw.isdigit():
                tg_id = int(raw)
                rows.extend(conn.execute("SELECT * FROM users WHERE tg_id=? OR key=?", (tg_id, user_key_from_tg(tg_id))).fetchall())
            else:
                await update.message.reply_text("Укажи @username или Telegram ID.")
                return

        unique = {r["key"]: r for r in rows}
        rows = list(unique.values())
        if not rows:
            await update.message.reply_text("Такого человека нет в /users.")
            return

        for r in rows:
            keys_to_delete = {r["key"]}
            if r["tg_id"]:
                keys_to_delete.add(user_key_from_tg(r["tg_id"]))
            if r["username"]:
                keys_to_delete.add(user_key_from_username(r["username"]))

            for key in keys_to_delete:
                conn.execute("DELETE FROM users WHERE key=?", (key,))
                if table_exists(conn, "team_roles"):
                    conn.execute("DELETE FROM team_roles WHERE target_key=?", (key,))

            if r["tg_id"]:
                conn.execute("DELETE FROM users WHERE tg_id=?", (r["tg_id"],))
                if table_exists(conn, "team_roles"):
                    conn.execute("DELETE FROM team_roles WHERE tg_id=?", (r["tg_id"],))
            if r["username"]:
                conn.execute("DELETE FROM users WHERE lower(username)=?", (r["username"].lower(),))
                if table_exists(conn, "team_roles"):
                    conn.execute("DELETE FROM team_roles WHERE lower(username)=?", (r["username"].lower(),))
        conn.commit()

    deleted = "\n".join(f"— {user_name_for_users_list(r)} — {html.escape(r['role'])}" for r in rows)
    log_action(update.effective_user.id, clean_name(update.effective_user), f"removerole {deleted}")
    await update.message.reply_text(
        "✅ Человек убран из команды проекта.\n\n" + deleted,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    update_existing_user_identity(q.from_user)
    data = q.data or ""

    if data == "info:opening":
        await q.message.reply_text(
            "📅 <b>Когда открытие?</b>\n\n"
            f"Разработка началась: <b>{parse_project_start_date().strftime('%d.%m.%Y')}</b>\n"
            "До открытия ещё: <b>неизвестно</b>",
            parse_mode="HTML",
        )
        return

    if data.startswith("task:done:"):
        task_id = int(data.rsplit(":", 1)[1])
        await done_task(q, context, task_id, q.from_user.id, clean_name(q.from_user))
        return

    await q.message.reply_text("Эта кнопка больше не используется.")


async def done_task(q, context: ContextTypes.DEFAULT_TYPE, task_id: int, actor_id: int, actor_name: str):
    with db() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            await q.edit_message_text("Задача не найдена.")
            return
        if task["status"] == "Выполнено":
            await q.edit_message_text("Эта задача уже выполнена.")
            return
        allowed = is_manager(actor_id) or task["assignee_tg_id"] == actor_id or task["assignee_key"] == user_key_from_tg(actor_id)
        if not allowed:
            await q.edit_message_text("Это не твоя задача.")
            return
        conn.execute("UPDATE tasks SET status='Выполнено', done_at=CURRENT_TIMESTAMP WHERE id=?", (task_id,))
        conn.commit()

    text = (
        "✅ <b>Задача выполнена</b>\n\n"
        f"#{task_id}\n"
        f"Исполнитель: {html.escape(task['assignee_name'] or 'Не указан')}\n"
        f"Отметил: {html.escape(actor_name)}\n\n"
        f"Задача: {html.escape(task['text'] or 'Без текста')}"
    )
    await q.edit_message_text(text, parse_mode="HTML")
    if ADMIN_GROUP_ID:
        try:
            await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=text, parse_mode="HTML")
        except Exception:
            pass



# -------------------- OPENMODEL / DEEPSEEK PRIVATE AI --------------------

def get_ai_history(user_id: int) -> list[dict[str, str]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT role, content
            FROM ai_private_messages
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, AI_HISTORY_LIMIT),
        ).fetchall()
    return [
        {"role": r["role"], "content": r["content"]}
        for r in reversed(rows)
        if r["role"] in {"user", "assistant"} and (r["content"] or "").strip()
    ]


def save_ai_message(user_id: int, role: str, content: str):
    content = (content or "").strip()
    if not content:
        return
    with db() as conn:
        conn.execute(
            "INSERT INTO ai_private_messages(user_id, role, content) VALUES(?,?,?)",
            (user_id, role, content[:8000]),
        )
        # Держим историю компактной, чтобы база не раздувалась.
        conn.execute(
            """
            DELETE FROM ai_private_messages
            WHERE user_id=? AND id NOT IN (
                SELECT id FROM ai_private_messages
                WHERE user_id=?
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (user_id, user_id, max(AI_HISTORY_LIMIT * 2, 20)),
        )
        conn.commit()


def extract_openmodel_text(payload: dict) -> str:
    parts = []
    for block in payload.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    text = "\n".join(p for p in parts if p).strip()
    return text or "Не получилось получить текст ответа от модели."


async def ask_openmodel_deepseek(user_id: int, user_text: str) -> str:
    messages = get_ai_history(user_id)
    messages.append({"role": "user", "content": user_text[:6000]})

    headers = {
        "Authorization": f"Bearer {OPENMODEL_API_KEY}",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": OPENMODEL_MODEL,
        "max_tokens": AI_MAX_TOKENS,
        "temperature": AI_TEMPERATURE,
        "system": AI_SYSTEM_PROMPT,
        "messages": messages,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{OPENMODEL_BASE_URL}/messages", headers=headers, json=body)

    if resp.status_code == 401:
        return "ИИ не отвечает: неверный OPENMODEL_API_KEY. Проверь ключ в .env."
    if resp.status_code == 429:
        return "ИИ временно ограничен по лимитам. Попробуй чуть позже."
    if resp.status_code >= 400:
        try:
            err = resp.json()
            message = err.get("error", {}).get("message") or err.get("message") or str(err)
        except Exception:
            message = resp.text[:500]
        return f"ИИ вернул ошибку: {html.escape(message)}"

    return extract_openmodel_text(resp.json())


async def private_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отвечает через DeepSeek только в ЛС. В users никого не добавляет."""
    if not update.effective_chat or update.effective_chat.type != "private":
        return
    if not update.effective_user or update.effective_user.is_bot:
        return
    if not update.effective_message or not update.effective_message.text:
        return

    # Только обновляем данные уже существующего участника команды, новых людей не создаём.
    update_existing_user_identity(update.effective_user)

    user_text = update.effective_message.text.strip()
    if not user_text:
        return

    if not OPENMODEL_API_KEY:
        await update.message.reply_text(
            "ИИ-ответы пока не подключены. Нужно добавить OPENMODEL_API_KEY в .env и перезапустить бота."
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        answer = await ask_openmodel_deepseek(update.effective_user.id, user_text)
    except httpx.TimeoutException:
        answer = "ИИ долго не отвечает. Попробуй ещё раз через минуту."
    except Exception as e:
        answer = f"Ошибка ИИ-модуля: {html.escape(str(e))}"

    save_ai_message(update.effective_user.id, "user", user_text)
    save_ai_message(update.effective_user.id, "assistant", answer)
    await update.message.reply_text(answer[:3900], disable_web_page_preview=True)


# -------------------- MESSAGE COUNT --------------------

async def count_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Считает сообщения в админ-чате. В users никого не добавляет."""
    if not ADMIN_GROUP_ID or not update.effective_chat or update.effective_chat.id != ADMIN_GROUP_ID:
        return
    if not update.effective_user or update.effective_user.is_bot:
        return
    if not update.effective_message:
        return

    user = update.effective_user
    update_existing_user_identity(user)

    username = (user.username or "").lower() or None
    display = clean_name(user)
    stat_date = today_iso()

    with db() as conn:
        conn.execute(
            """
            INSERT INTO message_stats(stat_date, chat_id, user_id, username, display_name, count)
            VALUES(?,?,?,?,?,1)
            ON CONFLICT(stat_date, chat_id, user_id) DO UPDATE SET
                username=excluded.username,
                display_name=excluded.display_name,
                count=count+1
            """,
            (stat_date, ADMIN_GROUP_ID, user.id, username, display),
        )
        conn.commit()


async def send_daily_stats(context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_GROUP_ID:
        return
    report_date = (datetime.now(TIMEZONE).date() - timedelta(days=1)).isoformat()
    text = format_top(report_date)
    text += f"\n\nСтарт разработки: <b>{parse_project_start_date().strftime('%d.%m.%Y')}</b>"
    await context.bot.send_message(chat_id=ADMIN_GROUP_ID, text=text, parse_mode="HTML", disable_web_page_preview=True)


# -------------------- MAIN --------------------

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Заполни BOT_TOKEN в .env")
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start), group=0)
    app.add_handler(CommandHandler("panel", panel), group=0)
    app.add_handler(CommandHandler("users", users_cmd), group=0)
    app.add_handler(CommandHandler("user", users_cmd), group=0)
    app.add_handler(CommandHandler("top", top_cmd), group=0)
    app.add_handler(CommandHandler("setrole", setrole), group=0)
    app.add_handler(CommandHandler("removerole", removerole), group=0)
    app.add_handler(CallbackQueryHandler(callbacks), group=0)

    # DeepSeek отвечает только в ЛС и только на обычный текст, команды не перехватывает.
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_ai_message), group=0)

    # Счётчик стоит отдельной группой: команды тоже считаются, но users не создаются.
    app.add_handler(MessageHandler(filters.ALL, count_admin_message), group=1)

    if ADMIN_GROUP_ID and app.job_queue:
        app.job_queue.run_daily(send_daily_stats, time=time(0, 0, tzinfo=TIMEZONE), name="daily_admin_message_stats")
    elif ADMIN_GROUP_ID and not app.job_queue:
        print("ВНИМАНИЕ: для ежедневной статистики установи python-telegram-bot[job-queue]")

    print("Elite Russia team bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
