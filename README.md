# Elite Russia Team Bot

Упрощённый Telegram-бот только для команды проекта.

## Команды

```text
/setrole @username название роли
/setrole 123456789 название роли
/users
```

Пример:

```text
/setrole @dskaksda разработчик
/setrole 123456789 основатель
```

`/users` покажет:

```text
👥 Команда проекта
@dskaksda — разработчик
123456789 — основатель

Всего в команде: 2 человек
```

## Доступ

В `.env` впиши Telegram ID тех, кто может выдавать роли:

```env
ROLE_MANAGER_IDS=123456789,987654321
```

Если нужен только ты — оставь только свой ID.

## Настройка

1. Переименуй `.env.example` в `.env`.
2. Впиши `BOT_TOKEN`.
3. Впиши `ADMIN_GROUP_ID`, если бот должен работать только в одном чате. Если не знаешь ID группы — оставь `0`.
4. Впиши `ROLE_MANAGER_IDS`.
5. Установи зависимости:

```bash
pip install -r requirements.txt
```

6. Запусти:

```bash
python main.py
```
