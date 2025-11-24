# tg-feedback-bot-ru

Телеграм‑бот для обработки пользовательских обращений: принимает сообщения из личных чатов, пересылает их администраторам и позволяет отвечать пользователям из приватных и групповых чатов. Хранение состояния всегда выполняется в PostgreSQL (SQLAlchemy + aiotgbot storage).

## Требования

- Python 3.13+
- PostgreSQL для хранения состояния

## Переменные окружения

Приложение полностью конфигурируется через env vars (см. `src/tg_feedback_bot_ru/settings.py`). Подготовьте файл `.env` или используйте пример `bot_env.example`.

| Переменная      | Назначение                                                                 |
|-----------------|-----------------------------------------------------------------------------|
| `TZ`            | Часовой пояс процесса (`Europe/Moscow`, `UTC` и т.д.).                      |
| `TG_TOKEN`      | Bot API token. Можно передать напрямую или через Docker secret.            |
| `ADMIN_USERNAME`| Telegram username администратора без `@`.                                   |
| `CHAT_LIST_SIZE`| Размер списка последних чатов для быстрых ответов (1–20).                   |
| `POSTGRES_DSN`  | DSN PostgreSQL в формате SQLAlchemy async (`postgresql+asyncpg://…`).       |

Все переменные обязательны (для `CHAT_LIST_SIZE` и `POSTGRES_DSN` есть значения по умолчанию, но в проде лучше задавать их явно).

## Локальная разработка

```bash
uv sync --group dev
uv run python -m tg_feedback_bot_ru
```

Полезные команды:

- `uv run ruff format --check src/tg_feedback_bot_ru`
- `uv run ruff check src/tg_feedback_bot_ru`
- `uv run mypy src/tg_feedback_bot_ru`
- `uv run basedpyright`

## Docker

Официальный образ собирается GitHub Actions из опубликованной версии PyPI. Локально можно:

```bash
docker build --build-arg BOT_VERSION=0.3.0 -t tg-feedback-bot-ru:0.3.0 .
docker run --env-file bot_env.example tg-feedback-bot-ru:0.3.0
```

В проде используйте образ `ghcr.io/gleb-chipiga/tg-feedback-bot-ru:<tag>` (теги совпадают с `v*` релизами). Он ожидает переменные окружения из таблицы выше и запускает консольный скрипт `tg-feedback-bot-ru`.
