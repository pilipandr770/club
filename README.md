# Club

Flask-приложение клуба. Архитектура — в [ARCHITECTURE.md](ARCHITECTURE.md), правила для разработки — в [CLAUDE.md](CLAUDE.md).

Сейчас готова фаза 0: каркас приложения, Docker Compose, Babel (`de`), реестр юрисдикций (`DE`), миграции, модели `User`, `Profile`, `Consent`.

## Локальный запуск

```bash
cp .env.example .env        # заполнить пароли и SECRET_KEY
docker compose up -d --build
```

Сайт: http://localhost:8081 (локально без TLS). Проверка: `/healthz`.

## Команды

Всё выполняется в контейнерах:

```bash
docker compose exec app pytest -q                 # тесты (нужна сборка с INSTALL_DEV=1)
docker compose exec app flask db migrate -m "..." # новая миграция
docker compose exec app flask db upgrade
docker compose exec app flask i18n extract       # строки → messages.pot
docker compose exec app flask i18n update        # → каталоги переводов
docker compose exec app flask i18n compile       # → .mo (без этого перевод не работает)

**Законы внутри клуба.** Корпус федерального права хранится в Postgres (таблицы `law_*`, pgvector). Команды:
`flask laws-import --laws-dir <папка> --qdrant <адрес>` (разовый перенос из старого сервиса), `flask laws-sync` (полная синхронизация
с gesetze-im-internet.de, только изменившиеся законы), `flask laws-status`. Раз в неделю синхронизацию запускает сервис `scheduler`.
Таблицы `law_*` не входят в бэкапы (`pg_dump -T 'law_*'`): они восстанавливаются из публичного источника. Для смыслового поиска нужен
`MISTRAL_API_KEY`; без него работает полнотекстовый поиск.
```

Новая строка в интерфейсе: `extract` → `update` → перевод в `translations/de/LC_MESSAGES/messages.po` → `compile`. Тест `test_german_catalog_is_complete` не пропустит непереведённую строку.

## Структура

- `app/` — фабрика приложения, конфиги, модели, blueprint `public`, реестр юрисдикций, Babel.
- `translations/` — каталоги переводов (`de` и `ru` включены; `en`/`he` позже).
- `migrations/` — Flask-Migrate. Тест `test_migrations_match_models` следит, чтобы миграции совпадали с моделями.
- `deploy/` — Caddyfile и init-скрипт Postgres (создаёт `club_test`).
- `tests/` — smoke, i18n, юрисдикции, модели.

## Что ещё не сделано (фазы 1+)

Фаза 1b: профили, матчинг, ассистент Clubi, сброс пароля, тарифы (`config/plans.py`, пока только гостевой). Redis, scheduler, backup, mailpit, `en`/`ru`.

## Регистрация и проверка личности

Порядок: регистрация (почта, пароль, согласия, страна проживания) → подтверждение почты → проверка личности через Stripe Identity → доступ (гостевой тариф).

- Клуб не хранит документы, селфи и даты рождения. Результат приходит вебхуком `POST /webhooks/stripe`: подпись проверяется, повторные события игнорируются.
- Страны из `BLOCKED_DOCUMENT_COUNTRIES` и `BLOCKED_RESIDENCE_COUNTRIES` не допускаются. Список задан в ARCHITECTURE.md §11.
- Удаление аккаунта сначала удаляет данные проверки у Stripe (redact), и только потом — у нас.

Для локальной проверки вебхуков нужны тестовые ключи Stripe в `.env` и Stripe CLI:

```bash
stripe listen --forward-to https://localhost/webhooks/stripe
```

Ключи, которые в `.env` пустые, отключают проверку: участник увидит сообщение «проверка сейчас недоступна».

## Русский интерфейс

В меню выберите «Русский» и нажмите «Сохранить». Язык сохраняется в cookie, а после входа — в профиле. При регистрации язык профиля берётся из текущего интерфейса. Письма участнику отправляются на языке профиля. По умолчанию остаётся немецкий; русский также выбирается по языку браузера. Юрисдикция при переключении не меняется. Подробности и проверки: [docs/RUSSIAN_LOCALIZATION.md](docs/RUSSIAN_LOCALIZATION.md).
