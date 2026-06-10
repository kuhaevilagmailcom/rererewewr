# Elite Russia Admin Telegram Bot

Безопасный бот для админ-чата: роли, должности, список известных участников и разрешённые команды управления сервером.

## Установка
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Заполни `.env`:
- `BOT_TOKEN` — токен от @BotFather
- `OWNER_ID` — твой Telegram ID
- `ADMIN_GROUP_ID` — ID админ-группы

Запуск:
```bash
python bot.py
```

## Команды
- `/start` — запуск
- `/help` — помощь
- `/me` — моя роль
- `/users` — известные пользователи группы
- `/settitle @user Главный администратор` — выдать название/должность
- `/setrole @user senior_admin` — выдать роль
- `/deladmin @user` — снять роль до user
- `/server` — статус сервера
- `/actions` — разрешённые действия
- `/do restart_game` — выполнить разрешённое действие

## Важно
Бот не выполняет произвольные shell-команды. Все действия сервера задаются в `config.json` allowlist-ом.
