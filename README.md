# Elite Russia Admin Telegram Bot

Бот для админ-чата Elite Russia: управление ролями бота, просмотр аккаунтов в базе, выдача админки/денег, логи игры, статус сервера и соцсети проекта.

## Установка

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python bot.py
```

В `.env` заполни:
- `BOT_TOKEN` — токен от @BotFather
- `OWNER_ID` — твой Telegram ID
- `ADMIN_GROUP_ID` — ID админ-группы
- `GAME_DB_PASSWORD` — пароль от MySQL

Пароль и токен нельзя хранить в `bot.py` и нельзя кидать в чат.

## Главное

Бот работает только с теми таблицами/колонками, которые указаны в `config.json`. Если в твоей базе таблица игроков называется не `accounts`, а например `users`, поменяй:

```json
"accounts_table": "accounts",
"login_column": "name",
"admin_column": "admin",
"money_column": "money"
```

Для логов тоже настрой:

```json
"logs_table": "logs",
"logs_text_column": "text",
"logs_time_column": "time"
```

## Команды

- `/help` — список команд
- `/me` — моя роль
- `/users` — известные пользователи группы
- `/settitle @user Главный администратор` — выдать название
- `/setrole @user helper|admin|senior_admin|chief_admin` — выдать права на управление ботом
- `/deladmin @user` — снять права бота
- `/botroles` — роли и доступы
- `/server` — статус VPS и подключение к MySQL
- `/account Nick_Name` — посмотреть аккаунт игрока
- `/accounts часть_ника` — поиск аккаунтов
- `/giveadmin Nick_Name 5 причина` — выдать админку в игре
- `/givemoney Nick_Name 100000 причина` — выдать деньги в игре
- `/gamelogs` — последние логи игры
- `/gamelogs Nick_Name` — поиск по логам
- `/audit` — кто что выдавал через бота
- `/social` — соцсети проекта
- `/actions` — список разрешённых серверных действий
- `/do restart_game` — выполнить разрешённое действие из allowlist

## Права

Настраиваются в `config.json`:

- `owner` — владелец, задаётся через `OWNER_ID`
- `chief_admin` — может выдавать права бота, админку и деньги
- `senior_admin` — средний уровень
- `admin` — просмотр логов
- `helper` — статус, пользователи, аккаунты
- `user` — без прав

## Безопасность

- Нет свободной команды `/exec`.
- Все действия логируются в `audit_log`.
- Выдача денег ограничена `max_money_per_command`.
- Уровень админки ограничен `max_admin_level`.
- Роль `owner` нельзя снять или выдать через чат.
- Telegram Bot API не отдаёт полный список участников группы. `/users` показывает тех, кто писал после добавления бота.
