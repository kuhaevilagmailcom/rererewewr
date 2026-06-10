import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "team.db"

load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0") or "0")

# ID тех, кто может выдавать роли. Пример: ROLE_MANAGER_IDS=123456789,987654321
ROLE_MANAGER_IDS = {
    int(x.strip())
    for x in os.getenv("ROLE_MANAGER_IDS", "").split(",")
    if x.strip().isdigit()
}


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS team_roles (
                target_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                role_text TEXT NOT NULL,
                set_by_id INTEGER NOT NULL,
                set_by_name TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def is_allowed(user_id: int) -> bool:
    return user_id in ROLE_MANAGER_IDS


def only_group(update: Update) -> bool:
    return ADMIN_GROUP_ID == 0 or update.effective_chat.id == ADMIN_GROUP_ID


def normalize_target(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if raw.startswith("@"):
        username = raw[1:].strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
            raise ValueError("Некорректный username. Пример: @username")
        return f"username:{username.lower()}", f"@{username}"

    if raw.isdigit():
        return f"id:{raw}", raw

    raise ValueError("Укажи @username или Telegram user_id.")


async def setrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not only_group(update):
        return

    user = update.effective_user
    if not user or not is_allowed(user.id):
        await update.message.reply_text("⛔️ У тебя нет доступа выдавать роли.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Использование:\n"
            "/setrole @username название роли\n"
            "/setrole 123456789 название роли"
        )
        return

    try:
        target_key, display_name = normalize_target(context.args[0])
    except ValueError as e:
        await update.message.reply_text(str(e))
        return

    role_text = " ".join(context.args[1:]).strip()
    if len(role_text) < 2 or len(role_text) > 80:
        await update.message.reply_text("Название роли должно быть от 2 до 80 символов.")
        return

    set_by_name = f"@{user.username}" if user.username else str(user.id)

    with db() as conn:
        conn.execute(
            """
            INSERT INTO team_roles(target_key, display_name, role_text, set_by_id, set_by_name, updated_at)
            VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(target_key) DO UPDATE SET
                display_name=excluded.display_name,
                role_text=excluded.role_text,
                set_by_id=excluded.set_by_id,
                set_by_name=excluded.set_by_name,
                updated_at=CURRENT_TIMESTAMP
            """,
            (target_key, display_name, role_text, user.id, set_by_name),
        )

    await update.message.reply_text(f"✅ {display_name} теперь: {role_text}")


async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not only_group(update):
        return

    with db() as conn:
        rows = conn.execute(
            "SELECT display_name, role_text FROM team_roles ORDER BY updated_at DESC"
        ).fetchall()

    if not rows:
        await update.message.reply_text("Команда проекта пока пустая.")
        return

    lines = ["👥 Команда проекта"]
    for row in rows:
        lines.append(f"{row['display_name']} — {row['role_text']}")
    lines.append("")
    lines.append(f"Всего в команде: {len(rows)} человек")

    await update.message.reply_text("\n".join(lines))


async def remember_reply_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Команда не нужна, просто чтобы бот спокойно игнорировал обычные сообщения.
    return


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Заполни BOT_TOKEN в .env")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("setrole", setrole))
    app.add_handler(CommandHandler("users", users))
    app.add_handler(MessageHandler(filters.ALL, remember_reply_users))

    print("Elite Russia Team Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
