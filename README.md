# Elite Russia Admin Telegram Bot v4

## Что изменено

- Владелец из `OWNER_ID` всегда имеет полный доступ к боту.
- Добавлена команда `/myid`, чтобы быстро узнать свой Telegram ID.
- `/access` теперь умеет выдавать доступ тремя способами:
  - `/access @username manager`
  - `/access 123456789 manager`
  - ответом на сообщение человека: `/access manager`
- `owner` нельзя выдать через чат. Полный владелец задаётся только через `.env` в `OWNER_ID`.
- Для полного управления через чат используй доступ `manager`.

## Доступы

Смотри `config.json`:

```json
"bot_access": {
  "owner": 100,
  "manager": 80,
  "moderator": 40,
  "viewer": 10,
  "none": 0
}
```

## Примеры

Выдать доступ по username:

```text
/access @limyzinc manager
```

Выдать доступ по Telegram ID:

```text
/access 8464597898 manager
```

Выдать доступ ответом на сообщение:

```text
/access manager
```

Снять доступ:

```text
/access @username none
```

Посмотреть себя:

```text
/me
/myid
```

## Важно

Если у тебя показывает `none / 0`, значит `OWNER_ID` в `.env` не совпадает с твоим настоящим Telegram ID. Узнай ID через `/myid` или @userinfobot, впиши его в `.env` и перезапусти бота.

Не кидай `.env` никому: там токен бота и пароль от MySQL.
