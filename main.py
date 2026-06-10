import json
import os
import sqlite3
import subprocess
from pathlib import Path

import psutil
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "bot.db"
CONFIG_PATH = BASE_DIR / "config.json"

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

ROLES = CONFIG["roles"]
ACTIONS = CONFIG["allowed_actions"]


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            title TEXT DEFAULT '',
            role TEXT DEFAULT 'user',
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO users(tg_id, username, first_name, title, role) VALUES(?,?,?,?,?)",
            (OWNER_ID, "", "Owner", "Владелец проекта", "owner"),
        )


def save_user(user):
    if not user:
        return
    role = "owner" if user.id == OWNER_ID else "user"
    with db() as conn:
        conn.execute(
            """
            INSERT INTO users(tg_id, username, first_name, role, last_seen)
            VALUES(?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(tg_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_seen=CURRENT_TIMESTAMP
            """,
            (user.id, user.username or "", user.first_name or "", role),
        )


def get_user(tg_id: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if row:
            return dict(row)
    return {"tg_id": tg_id, "role": "user", "title": ""}


def role_power(role: str) -> int:
    return int(ROLES.get(role, 0))


def require_group(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.id == ADMIN_GROUP_ID


def has_role(tg_id: int, min_role: str) -> bool:
    user = get_user(tg_id)
    return role_power(user.get("role", "user")) >= role_power(min_role)


async def deny(update: Update, text="Недостаточно прав."):
    await update.message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    await update.message.reply_text("Компьютер/серверный админ-бот подключен. Напиши /help")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    await update.message.reply_text(
        "Команды:\n"
        "/me — моя роль\n"
        "/users — пользователи группы\n"
        "/settitle @user Название — выдать должность\n"
        "/setrole @user admin|helper|senior_admin|chief_admin — выдать роль\n"
        "/deladmin @user — снять админку\n"
        "/server — статус сервера\n"
        "/actions — список разрешённых действий\n"
        "/do action_name — выполнить действие"
    )


async def track_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    u = get_user(update.effective_user.id)
    await update.message.reply_text(f"Твоя роль: {u.get('role')}\nНазвание: {u.get('title') or 'не задано'}")


async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    if not require_group(update):
        return await deny(update, "Эта команда работает только в админ-группе.")
    if not has_role(update.effective_user.id, "helper"):
        return await deny(update)
    with db() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY role DESC, last_seen DESC LIMIT 100").fetchall()
    lines = []
    for r in rows:
        name = f"@{r['username']}" if r['username'] else r['first_name'] or str(r['tg_id'])
        lines.append(f"{name} — {r['role']} — {r['title'] or 'без названия'}")
    await update.message.reply_text("Пользователи:\n" + "\n".join(lines))


def parse_target_and_text(args):
    if len(args) < 2:
        return None, None
    target = args[0].lstrip("@")
    text = " ".join(args[1:]).strip()
    return target, text


def find_by_username(username: str):
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()


async def settitle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    if not has_role(update.effective_user.id, "senior_admin"):
        return await deny(update)
    username, title = parse_target_and_text(context.args)
    if not username or not title:
        return await update.message.reply_text("Пример: /settitle @nickname Главный администратор")
    target = find_by_username(username)
    if not target:
        return await update.message.reply_text("Пользователь не найден. Пусть он напишет любое сообщение в группе.")
    with db() as conn:
        conn.execute("UPDATE users SET title=? WHERE tg_id=?", (title, target["tg_id"]))
    await update.message.reply_text(f"Готово: @{username} теперь '{title}'.")


async def setrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    if not has_role(update.effective_user.id, "chief_admin"):
        return await deny(update)
    username, role = parse_target_and_text(context.args)
    if not username or role not in ROLES or role == "owner":
        return await update.message.reply_text("Пример: /setrole @nickname admin|helper|senior_admin|chief_admin")
    target = find_by_username(username)
    if not target:
        return await update.message.reply_text("Пользователь не найден. Пусть он напишет любое сообщение в группе.")
    if target["tg_id"] == OWNER_ID:
        return await update.message.reply_text("Роль владельца менять нельзя.")
    with db() as conn:
        conn.execute("UPDATE users SET role=? WHERE tg_id=?", (role, target["tg_id"]))
    await update.message.reply_text(f"Готово: @{username} получил роль {role}.")


async def deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    if not has_role(update.effective_user.id, "chief_admin"):
        return await deny(update)
    if not context.args:
        return await update.message.reply_text("Пример: /deladmin @nickname")
    username = context.args[0].lstrip("@")
    target = find_by_username(username)
    if not target:
        return await update.message.reply_text("Пользователь не найден.")
    if target["tg_id"] == OWNER_ID:
        return await update.message.reply_text("Владельца снять нельзя.")
    with db() as conn:
        conn.execute("UPDATE users SET role='user' WHERE tg_id=?", (target["tg_id"],))
    await update.message.reply_text(f"Готово: @{username} теперь обычный пользователь.")


async def server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    if not has_role(update.effective_user.id, "helper"):
        return await deny(update)
    cpu = psutil.cpu_percent(interval=0.4)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage(str(BASE_DIR.anchor or "/"))
    await update.message.reply_text(
        f"Сервер:\nCPU: {cpu}%\nRAM: {ram.percent}%\nDISK: {disk.percent}%"
    )


async def actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    lines = [f"{name} — {data['description']} — от {data['min_role']}" for name, data in ACTIONS.items()]
    await update.message.reply_text("Разрешённые действия:\n" + "\n".join(lines))


async def do_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    if not context.args:
        return await update.message.reply_text("Пример: /do restart_game")
    name = context.args[0]
    action = ACTIONS.get(name)
    if not action:
        return await update.message.reply_text("Такого действия нет. Напиши /actions")
    if not has_role(update.effective_user.id, action["min_role"]):
        return await deny(update)
    # Важно: только allowlist-команды из config.json, без пользовательского ввода в shell.
    result = subprocess.run(action["command"], shell=True, capture_output=True, text=True, timeout=20)
    out = (result.stdout or result.stderr or "Команда выполнена.").strip()
    await update.message.reply_text(out[:3500])


def main():
    if not BOT_TOKEN or not OWNER_ID or not ADMIN_GROUP_ID:
        raise SystemExit("Заполни BOT_TOKEN, OWNER_ID, ADMIN_GROUP_ID в .env")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("me", me))
    app.add_handler(CommandHandler("users", users))
    app.add_handler(CommandHandler("settitle", settitle))
    app.add_handler(CommandHandler("setrole", setrole))
    app.add_handler(CommandHandler("deladmin", deladmin))
    app.add_handler(CommandHandler("server", server))
    app.add_handler(CommandHandler("actions", actions))
    app.add_handler(CommandHandler("do", do_action))
    app.add_handler(MessageHandler(filters.ALL, track_members))
    print("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
