import json
import os
import re
import socket
import sqlite3
from pathlib import Path
from typing import Optional

import mysql.connector
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
BOT_DB_PATH = BASE_DIR / "bot.db"

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0") or "0")

GAME_DB = {
    "host": os.getenv("GAME_DB_HOST", "").strip(),
    "port": int(os.getenv("GAME_DB_PORT", "3306") or "3306"),
    "database": os.getenv("GAME_DB_NAME", "").strip(),
    "user": os.getenv("GAME_DB_USER", "").strip(),
    "password": os.getenv("GAME_DB_PASSWORD", "").strip(),
}

GAME_SERVER_HOST = os.getenv("GAME_SERVER_HOST", "").strip()
GAME_SERVER_PORT = os.getenv("GAME_SERVER_PORT", "").strip()

CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
PROJECT = CONFIG["project"]
ACCESS = CONFIG["bot_access"]
PERMISSIONS = CONFIG["permissions"]
GDB = CONFIG["game_db"]
LIMITS = CONFIG["limits"]


def q(name: str) -> str:
    """Безопасно экранируем имена таблиц/колонок из config.json."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(f"Некорректное имя в config.json: {name}")
    return f"`{name}`"


def bot_db():
    conn = sqlite3.connect(BOT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def game_db():
    if not GAME_DB["host"] or not GAME_DB["database"] or not GAME_DB["user"] or not GAME_DB["password"]:
        raise RuntimeError("Заполни GAME_DB_* в .env")
    return mysql.connector.connect(**GAME_DB, connection_timeout=8)


def init_db():
    with bot_db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS telegram_users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            access TEXT DEFAULT 'none',
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS player_roles (
            nickname TEXT PRIMARY KEY,
            role_text TEXT NOT NULL,
            set_by_id INTEGER,
            set_by_username TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            username TEXT,
            action TEXT,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        if OWNER_ID:
            conn.execute("""
            INSERT OR IGNORE INTO telegram_users(tg_id, username, first_name, access)
            VALUES(?,?,?,?)
            """, (OWNER_ID, "", "Owner", "owner"))


def audit(user, action: str, details: str):
    with bot_db() as conn:
        conn.execute(
            "INSERT INTO audit_log(tg_id, username, action, details) VALUES(?,?,?,?)",
            (user.id if user else 0, user.username or "" if user else "", action, details[:2000]),
        )


def save_tg_user(user):
    if not user:
        return
    access = "owner" if user.id == OWNER_ID else "none"
    with bot_db() as conn:
        current = conn.execute("SELECT access FROM telegram_users WHERE tg_id=?", (user.id,)).fetchone()
        if current and user.id != OWNER_ID:
            access = current["access"]
        conn.execute("""
        INSERT INTO telegram_users(tg_id, username, first_name, access, last_seen)
        VALUES(?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(tg_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            last_seen=CURRENT_TIMESTAMP
        """, (user.id, user.username or "", user.first_name or "", access))


def access_name(tg_id: int) -> str:
    if tg_id == OWNER_ID:
        return "owner"
    with bot_db() as conn:
        row = conn.execute("SELECT access FROM telegram_users WHERE tg_id=?", (tg_id,)).fetchone()
    return row["access"] if row else "none"


def access_level(access: str) -> int:
    return int(ACCESS.get(access, 0))


def has_perm(tg_id: int, perm: str) -> bool:
    return access_level(access_name(tg_id)) >= int(PERMISSIONS.get(perm, 100))


async def deny(update: Update, text: str = "⛔️ Нет прав на эту команду."):
    await update.effective_message.reply_text(text)


def only_group(update: Update) -> bool:
    return ADMIN_GROUP_ID == 0 or update.effective_chat.id == ADMIN_GROUP_ID


async def guard(update: Update, perm: Optional[str] = None) -> bool:
    save_tg_user(update.effective_user)
    if not only_group(update):
        await update.effective_message.reply_text("⛔️ Бот работает только в указанном админ-чате.")
        return False
    if perm and not has_perm(update.effective_user.id, perm):
        await deny(update)
        return False
    return True


def find_mentioned_user(text: str):
    m = re.search(r"@([A-Za-z0-9_]{3,})", text or "")
    if not m:
        return None
    username = m.group(1).lower()
    with bot_db() as conn:
        row = conn.execute("SELECT * FROM telegram_users WHERE lower(username)=?", (username,)).fetchone()
    return row


def find_user_by_id(tg_id: int):
    with bot_db() as conn:
        return conn.execute("SELECT * FROM telegram_users WHERE tg_id=?", (tg_id,)).fetchone()


def ensure_tg_user(tg_id: int, username: str = "", first_name: str = ""):
    access = "owner" if tg_id == OWNER_ID else "none"
    with bot_db() as conn:
        current = conn.execute("SELECT access FROM telegram_users WHERE tg_id=?", (tg_id,)).fetchone()
        if current and tg_id != OWNER_ID:
            access = current["access"]
        conn.execute("""
        INSERT INTO telegram_users(tg_id, username, first_name, access, last_seen)
        VALUES(?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(tg_id) DO UPDATE SET
            username=CASE WHEN excluded.username != '' THEN excluded.username ELSE telegram_users.username END,
            first_name=CASE WHEN excluded.first_name != '' THEN excluded.first_name ELSE telegram_users.first_name END,
            last_seen=CURRENT_TIMESTAMP
        """, (tg_id, username or "", first_name or "", access))
    return find_user_by_id(tg_id)


def resolve_access_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Кого меняем в /access:
    1) reply на сообщение: /access manager
    2) username: /access @username manager
    3) Telegram ID: /access 123456789 manager
    Возвращает (row, new_access, human_name).
    """
    args = context.args or []
    if update.message and update.message.reply_to_message:
        if len(args) < 1:
            return None, None, None
        u = update.message.reply_to_message.from_user
        row = ensure_tg_user(u.id, u.username or "", u.first_name or "")
        return row, args[0].strip(), f"@{u.username}" if u.username else str(u.id)

    if len(args) < 2:
        return None, None, None

    target_raw = args[0].strip()
    new_access = args[1].strip()

    if target_raw.startswith("@"):
        row = find_mentioned_user(target_raw)
        human = target_raw
        return row, new_access, human

    if re.fullmatch(r"\d{5,20}", target_raw):
        tg_id = int(target_raw)
        row = ensure_tg_user(tg_id)
        return row, new_access, str(tg_id)

    return None, new_access, target_raw


def format_access_list() -> str:
    return "\n".join([f"• {name} — {level}" for name, level in sorted(ACCESS.items(), key=lambda x: -x[1])])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.message.reply_text(
        f"✅ {PROJECT['name']} Admin Bot подключен.\n\n"
        "Главное:\n"
        "• /server — онлайн и работает ли сервер\n"
        "• /role Nick_Name Главный разработчик — записать роль игроку свободным текстом\n"
        "• /getrole Nick_Name — посмотреть роль игрока\n"
        "• /access @user manager — выдать доступ к боту\n"
        "• /access 123456789 manager — выдать доступ по Telegram ID\n"
        "• ответ на сообщение: /access manager — выдать доступ человеку\n"
        "• /account Nick_Name — аккаунт игрока\n"
        "• /giveadmin Nick_Name 5 причина — выдать админку\n"
        "• /givemoney Nick_Name 100000 причина — выдать деньги\n"
        "• /gamelogs Nick_Name — логи"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    a = access_name(update.effective_user.id)
    await update.message.reply_text(f"👤 Ты: @{update.effective_user.username or update.effective_user.id}\n🔑 Доступ к боту: {a} / {access_level(a)}")


async def access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "manage_bot_access"):
        return

    target, new_access, human = resolve_access_target(update, context)

    if not target or not new_access:
        await update.message.reply_text(
            "Использование:\n"
            "/access @username manager\n"
            "/access 123456789 manager\n"
            "Ответом на сообщение: /access manager\n\n"
            "Снять доступ:\n"
            "/access @username none\n\n"
            f"Доступы:\n{format_access_list()}\n\n"
            "Важно: по @username человек должен хотя бы раз написать в чат. По Telegram ID можно выдать сразу."
        )
        return

    if new_access not in ACCESS:
        await update.message.reply_text(f"Нет такого доступа. Доступные:\n{format_access_list()}")
        return

    target_id = int(target["tg_id"])

    if target_id == OWNER_ID:
        await update.message.reply_text("Владельцу нельзя менять доступ через чат. У владельца всегда полный доступ.")
        return

    if new_access == "owner":
        await update.message.reply_text("Owner задаётся только через OWNER_ID в .env. Для полного управления через чат используй manager.")
        return

    with bot_db() as conn:
        conn.execute("UPDATE telegram_users SET access=? WHERE tg_id=?", (new_access, target_id))

    username = target["username"] or ""
    shown = f"@{username}" if username else (human or str(target_id))
    audit(update.effective_user, "access", f"{shown} ({target_id}) -> {new_access}")
    await update.message.reply_text(
        f"✅ Доступ к боту выдан\n"
        f"👤 Пользователь: {shown}\n"
        f"🆔 Telegram ID: {target_id}\n"
        f"🔑 Доступ: {new_access} / {access_level(new_access)}"
    )


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    u = update.effective_user
    await update.message.reply_text(
        f"👤 Ты: @{u.username or u.id}\n"
        f"🆔 Telegram ID: {u.id}\n"
        f"🔑 Доступ: {access_name(u.id)} / {access_level(access_name(u.id))}"
    )


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "view_accounts"):
        return
    with bot_db() as conn:
        rows = conn.execute("""
        SELECT tg_id, username, first_name, access, last_seen
        FROM telegram_users
        ORDER BY last_seen DESC
        LIMIT 50
        """).fetchall()
    if not rows:
        await update.message.reply_text("Пока никого не знаю. Telegram не отдаёт полный список группы, бот видит тех, кто писал.")
        return
    lines = ["👥 Пользователи, которых видел бот:"]
    for r in rows:
        name = f"@{r['username']}" if r["username"] else (r["first_name"] or str(r["tg_id"]))
        lines.append(f"• {name} — доступ: {r['access']}")
    await update.message.reply_text("\n".join(lines))


async def set_player_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "set_player_role"):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Использование:\n/role Nick_Name Главный разработчик\n/role Nick_Name 3D-моделлер\n/role Nick_Name Тим-лид")
        return

    nickname = context.args[0].strip()
    role_text = " ".join(context.args[1:]).strip()

    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9_\[\]\-]{2,32}", nickname):
        await update.message.reply_text("Некорректный ник. Пример: Nick_Name")
        return
    if len(role_text) < 2 or len(role_text) > 80:
        await update.message.reply_text("Роль должна быть от 2 до 80 символов.")
        return

    with bot_db() as conn:
        conn.execute("""
        INSERT INTO player_roles(nickname, role_text, set_by_id, set_by_username, updated_at)
        VALUES(?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(nickname) DO UPDATE SET
            role_text=excluded.role_text,
            set_by_id=excluded.set_by_id,
            set_by_username=excluded.set_by_username,
            updated_at=CURRENT_TIMESTAMP
        """, (nickname, role_text, update.effective_user.id, update.effective_user.username or ""))

    audit(update.effective_user, "set_player_role", f"{nickname} -> {role_text}")
    await update.message.reply_text(f"✅ Игроку {nickname} выдана роль:\n📌 {role_text}")


async def get_player_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "view_accounts"):
        return
    if not context.args:
        await update.message.reply_text("Использование: /getrole Nick_Name")
        return
    nickname = context.args[0].strip()
    with bot_db() as conn:
        row = conn.execute("SELECT * FROM player_roles WHERE lower(nickname)=lower(?)", (nickname,)).fetchone()
    if not row:
        await update.message.reply_text(f"У игрока {nickname} роль не записана.")
        return
    await update.message.reply_text(f"👤 {row['nickname']}\n📌 Роль: {row['role_text']}\n✍️ Выдал: @{row['set_by_username'] or row['set_by_id']}\n🕒 {row['updated_at']}")


async def team_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "view_accounts"):
        return
    with bot_db() as conn:
        rows = conn.execute("SELECT * FROM player_roles ORDER BY updated_at DESC LIMIT 100").fetchall()
    if not rows:
        await update.message.reply_text("Пока роли игроков не записаны.")
        return
    lines = ["📋 Команда / роли игроков:"]
    for r in rows:
        lines.append(f"• {r['nickname']} — {r['role_text']}")
    await update.message.reply_text("\n".join(lines))


def tcp_check(host: str, port: str) -> bool:
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=3):
            return True
    except Exception:
        return False


async def server_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "view_server"):
        return

    works = False
    online = 0
    error = ""

    try:
        with game_db() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(f"SELECT COUNT(*) AS online FROM {q(GDB['accounts_table'])} WHERE {q(GDB['online_column'])} > 0")
            row = cur.fetchone()
            online = int(row["online"] or 0)
            works = True
    except Exception as e:
        error = str(e)[:180]

    if GAME_SERVER_HOST and GAME_SERVER_PORT:
        works = tcp_check(GAME_SERVER_HOST, GAME_SERVER_PORT)

    await update.message.reply_text(
        "🟢 Сервер работает\n"
        f"👥 Онлайн: {online}"
        if works else
        "🔴 Сервер не работает\n"
        f"👥 Онлайн: {online}\n"
        f"Ошибка: {error or 'порт/БД недоступны'}"
    )


async def account_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "view_accounts"):
        return
    if not context.args:
        await update.message.reply_text("Использование: /account Nick_Name")
        return
    nick = context.args[0].strip()
    try:
        with game_db() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                f"""SELECT {q(GDB['id_column'])} AS id,
                          {q(GDB['login_column'])} AS name,
                          {q(GDB['admin_column'])} AS admin,
                          {q(GDB['cash_column'])} AS cash,
                          {q(GDB['bank_column'])} AS bank,
                          {q(GDB['level_column'])} AS level,
                          {q(GDB['online_column'])} AS online,
                          {q(GDB['ip_column'])} AS ip,
                          {q(GDB['mail_column'])} AS mail
                   FROM {q(GDB['accounts_table'])}
                   WHERE {q(GDB['login_column'])}=%s
                   LIMIT 1""",
                (nick,),
            )
            row = cur.fetchone()
        if not row:
            await update.message.reply_text("Аккаунт не найден.")
            return
        with bot_db() as bconn:
            role = bconn.execute("SELECT role_text FROM player_roles WHERE lower(nickname)=lower(?)", (nick,)).fetchone()
        role_text = role["role_text"] if role else "не указана"
        await update.message.reply_text(
            f"👤 Аккаунт: {row['name']}\n"
            f"🆔 ID: {row['id']}\n"
            f"📌 Роль проекта: {role_text}\n"
            f"🛡 Админка: {row['admin']}\n"
            f"⭐ Уровень: {row['level']}\n"
            f"💵 Наличные: {row['cash']}\n"
            f"🏦 Банк: {row['bank']}\n"
            f"🟢 Онлайн: {'да' if int(row['online'] or 0) > 0 else 'нет'}\n"
            f"📧 Почта: {row['mail'] or '-'}\n"
            f"🌐 IP: {row['ip'] or '-'}"
        )
    except Exception as e:
        await update.message.reply_text(f"Ошибка БД: {e}")


async def accounts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "view_accounts"):
        return
    if not context.args:
        await update.message.reply_text("Использование: /accounts часть_ника")
        return
    part = context.args[0].strip()
    limit = int(LIMITS["max_account_rows"])
    try:
        with game_db() as conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                f"""SELECT {q(GDB['id_column'])} AS id,
                          {q(GDB['login_column'])} AS name,
                          {q(GDB['admin_column'])} AS admin,
                          {q(GDB['level_column'])} AS level,
                          {q(GDB['online_column'])} AS online
                   FROM {q(GDB['accounts_table'])}
                   WHERE {q(GDB['login_column'])} LIKE %s
                   ORDER BY {q(GDB['id_column'])} DESC
                   LIMIT {limit}""",
                (f"%{part}%",),
            )
            rows = cur.fetchall()
        if not rows:
            await update.message.reply_text("Ничего не найдено.")
            return
        lines = ["🔎 Найденные аккаунты:"]
        for r in rows:
            lines.append(f"• {r['name']} | ID {r['id']} | lvl {r['level']} | admin {r['admin']} | {'online' if int(r['online'] or 0)>0 else 'offline'}")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Ошибка БД: {e}")


async def giveadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "give_game_admin"):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /giveadmin Nick_Name 5 причина")
        return
    nick = context.args[0].strip()
    try:
        level = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Уровень админки должен быть числом.")
        return
    if level < 0 or level > int(LIMITS["max_admin_level"]):
        await update.message.reply_text(f"Уровень должен быть от 0 до {LIMITS['max_admin_level']}.")
        return
    reason = " ".join(context.args[2:]) or "без причины"
    try:
        with game_db() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {q(GDB['accounts_table'])} SET {q(GDB['admin_column'])}=%s WHERE {q(GDB['login_column'])}=%s LIMIT 1",
                (level, nick),
            )
            conn.commit()
            changed = cur.rowcount
        if not changed:
            await update.message.reply_text("Аккаунт не найден или значение уже такое же.")
            return
        audit(update.effective_user, "giveadmin", f"{nick} -> {level}; {reason}")
        await update.message.reply_text(f"✅ {nick} выдана админка: {level}\nПричина: {reason}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка БД: {e}")


async def givemoney_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "give_money"):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /givemoney Nick_Name 100000 причина")
        return
    nick = context.args[0].strip()
    try:
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Сумма должна быть числом.")
        return
    max_amount = int(LIMITS["max_money_per_command"])
    if amount == 0 or abs(amount) > max_amount:
        await update.message.reply_text(f"Сумма должна быть от -{max_amount} до {max_amount}, кроме 0.")
        return
    reason = " ".join(context.args[2:]) or "без причины"
    try:
        with game_db() as conn:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {q(GDB['accounts_table'])} SET {q(GDB['cash_column'])}={q(GDB['cash_column'])}+%s WHERE {q(GDB['login_column'])}=%s LIMIT 1",
                (amount, nick),
            )
            conn.commit()
            changed = cur.rowcount
        if not changed:
            await update.message.reply_text("Аккаунт не найден.")
            return
        audit(update.effective_user, "givemoney", f"{nick} -> {amount}; {reason}")
        await update.message.reply_text(f"✅ {nick} выдано денег: {amount}\nПричина: {reason}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка БД: {e}")


async def gamelogs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "view_game_logs"):
        return
    search = " ".join(context.args).strip()
    limit = int(LIMITS["max_log_rows"])
    try:
        with game_db() as conn:
            cur = conn.cursor(dictionary=True)
            if search:
                cur.execute(
                    f"""SELECT {q(GDB['logs_id_column'])} AS id,
                              {q(GDB['logs_text_column'])} AS text,
                              {q(GDB['logs_time_column'])} AS time
                       FROM {q(GDB['logs_table'])}
                       WHERE {q(GDB['logs_text_column'])} LIKE %s
                       ORDER BY {q(GDB['logs_id_column'])} DESC
                       LIMIT {limit}""",
                    (f"%{search}%",),
                )
            else:
                cur.execute(
                    f"""SELECT {q(GDB['logs_id_column'])} AS id,
                              {q(GDB['logs_text_column'])} AS text,
                              {q(GDB['logs_time_column'])} AS time
                       FROM {q(GDB['logs_table'])}
                       ORDER BY {q(GDB['logs_id_column'])} DESC
                       LIMIT {limit}"""
                )
            rows = cur.fetchall()
        if not rows:
            await update.message.reply_text("Логи не найдены.")
            return
        lines = ["📜 Логи игры:"]
        for r in rows:
            lines.append(f"#{r['id']} | {r['time']}\n{str(r['text'])[:250]}")
        await update.message.reply_text("\n\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Ошибка БД/логов: {e}")


async def audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, "manage_bot_access"):
        return
    with bot_db() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 30").fetchall()
    if not rows:
        await update.message.reply_text("Аудит пуст.")
        return
    lines = ["🧾 Аудит бота:"]
    for r in rows:
        who = f"@{r['username']}" if r["username"] else str(r["tg_id"])
        lines.append(f"#{r['id']} | {r['created_at']} | {who}\n{r['action']}: {r['details']}")
    await update.message.reply_text("\n\n".join(lines))


async def social_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.message.reply_text(
        f"💬 Telegram: {PROJECT['telegram']}\n"
        f"💙 Вконтакте: {PROJECT['vk']}\n"
        f"🔵 Discord: {PROJECT['discord']}\n"
        f"🤩 TikTok: {PROJECT['tiktok']}\n"
        f"⛔️ YouTube: {PROJECT['youtube']}\n"
        f"🌐 Сайт: {PROJECT['site']}"
    )


async def remember_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_tg_user(update.effective_user)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Заполни BOT_TOKEN в .env")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("me", me))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("access", access_cmd))
    app.add_handler(CommandHandler("users", users_cmd))

    app.add_handler(CommandHandler("server", server_cmd))
    app.add_handler(CommandHandler("role", set_player_role))
    app.add_handler(CommandHandler("getrole", get_player_role))
    app.add_handler(CommandHandler("team", team_cmd))

    app.add_handler(CommandHandler("account", account_cmd))
    app.add_handler(CommandHandler("accounts", accounts_cmd))
    app.add_handler(CommandHandler("giveadmin", giveadmin_cmd))
    app.add_handler(CommandHandler("givemoney", givemoney_cmd))
    app.add_handler(CommandHandler("gamelogs", gamelogs_cmd))
    app.add_handler(CommandHandler("audit", audit_cmd))
    app.add_handler(CommandHandler("social", social_cmd))

    app.add_handler(MessageHandler(filters.ALL, remember_messages))
    print("Elite Russia Admin Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
