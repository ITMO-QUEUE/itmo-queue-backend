# ITMO.Queue backend

Минимальная инфраструктурная основа модульного монолита: FastAPI, PostgreSQL,
SQLAlchemy 2.x, Alembic и Redis. Бизнес-модули пока не реализованы.

## Требования

- Python 3.12+ и pip (Docker и CI используют Python 3.12).
- Docker с запущенным Docker Engine / Docker Desktop и Compose v2+.
- Git. Команды ниже выполняются из корня репозитория.

## Локальный запуск

Создайте виртуальное окружение:

```sh
python -m venv .venv
```

Активируйте его в своей оболочке:

| Оболочка | Команда |
| --- | --- |
| PowerShell (Windows) | `.\.venv\Scripts\Activate.ps1` |
| Git Bash (Windows) | `source .venv/Scripts/activate` |
| Bash / Zsh (Linux, macOS) | `source .venv/bin/activate` |

Если PowerShell запрещает активацию, вместо `python` можно вызывать
`.\.venv\Scripts\python.exe` без изменения политики выполнения.

```sh
python -m pip install -e ".[dev]"
```

Скопируйте конфигурацию:

```sh
# Git Bash / Linux / macOS
cp .env.example .env
```

```powershell
# PowerShell
Copy-Item .env.example .env
```

Задайте собственное локальное значение `POSTGRES_PASSWORD` в `.env`.
Этот файл игнорируется Git и исключён из Docker build context.
Приложение читает `.env` из текущего каталога; переменные процесса имеют приоритет.
Пароль передаётся SQLAlchemy через `URL.create`, URL-кодирование не требуется.
`ENVIRONMENT` принимает `development`, `test`, `production`;
`LOG_LEVEL` — `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.

Поднимите PostgreSQL и Redis и запустите API:

```sh
docker compose up -d --wait postgres redis
python -m uvicorn itmo_queue.main:app --reload
```

- [Health](http://localhost:8000/health) → `{"status":"ok"}`.
- [Swagger UI](http://localhost:8000/docs).
- [ReDoc](http://localhost:8000/redoc).
- [OpenAPI JSON](http://localhost:8000/openapi.json).

`/health` проверяет только HTTP-приложение и работает без PostgreSQL и Redis.
Проверки готовности этих сервисов выполняет Compose. Подключения приложения
ленивые: ошибка сервиса проявится при первой операции с ним.
Swagger UI и ReDoc загружают свои стандартные ресурсы из CDN.

## Запуск API в Docker

После создания `.env`:

```sh
docker compose --profile app up -d --build --wait
docker compose logs -f backend
```

API доступен на порту 8000. В контейнере адреса PostgreSQL и Redis автоматически
заменяются на имена сервисов; локальный `.env` сохраняет `localhost`.
Порты контейнеров опубликованы только на `127.0.0.1`.
Профиль `app` опционален; контейнер API запускается без autoreload.

Остановить окружение, сохранив данные PostgreSQL:

```sh
docker compose --profile app down
```

Именованный том `postgres_data` хранит PostgreSQL между перезапусками.
Redis используется только для временных данных и не имеет постоянного тома.
Изменение пароля в `.env` не меняет пароль уже инициализированной базы в томе.
Эта Compose-конфигурация предназначена для локальной разработки.

## Проверки

```sh
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -c "from itmo_queue.main import app; assert app.openapi()['paths']['/health']"
python -m alembic upgrade head --sql
docker compose --env-file .env.example config --quiet
```

Тесты используют HTTPX AsyncClient с ASGITransport и проверяют приложение,
конфигурацию, схему и CRUD студентов. Для CRUD-тестов необходим работающий
PostgreSQL с применёнными миграциями; Redis для этих тестов не требуется.
GitHub Actions поднимает PostgreSQL и применяет миграции перед тестами.

## Сессионная авторизация

`POST /api/v1/auth/login` принимает JSON `{"login":"...","password":"..."}`.
Для действующего пользователя API создаёт сессию в Redis и отправляет cookie
`itmo_session` с атрибутами HttpOnly и SameSite=Lax. Клиент отправляет cookie
в последующих запросах; `GET /api/v1/auth/me` возвращает текущего пользователя,
а `POST /api/v1/auth/logout` удаляет сессию и cookie. Время жизни задаётся
`SESSION_TTL_SECONDS` (по умолчанию 86400 секунд); повторный вход заменяет
текущую сессию. При истечении срока, удалении сессии, блокировке или удалении
пользователя требуется новый вход. Роль проверяется по базе при каждом запросе.

По умолчанию cookie имеет флаг Secure и передаётся только по HTTPS. Для локальной
разработки по HTTP можно задать `SESSION_COOKIE_SECURE=false`. При использовании
браузерного клиента отправляйте запросы с credentials и размещайте его на том же
сайте, что и API. Хранилище Redis должно быть доступно приложению: при его
перезапуске без сохранения данных пользователям потребуется войти снова.

Операции `/api/v1/students` доступны только пользователю с ролью `admin`.
Старый заголовок `X-Admin-Token` больше не поддерживается. Учётная запись
администратора должна быть заранее создана в базе с ролью `admin` и паролем,
хешированным функцией `get_password_hash`. Маршруты групп пока публичные.

## Миграции

Alembic использует ту же конфигурацию PostgreSQL, что и приложение.
Схема очередей создаётся ревизией `0001_initial`, затем ревизия
`5524e0c29d8a` добавляет группы и приводит пользователей к модели CRUD.
Существующие данные пользователей из `0001_initial` сохраняются, в том числе
статус блокировки. Перед обновлением базы сохраните резервную копию: поля
имени, логина и хеша пароля ограничиваются 255, 100 и 255 символами.

Ранее ревизия `5524e0c29d8a` существовала как независимая начальная миграция.
Если она уже применена к существующей базе, не запускайте обновление по этой
истории автоматически: необходимо отдельно согласовать фактическую схему и
данные с новой последовательностью миграций. Для пустой базы и базы на ревизии
`0001_initial` используйте `upgrade head`.

Для будущего модуля:

1. Наследуйте SQLAlchemy-модели от `itmo_queue.db.base.Base`.
2. Импортируйте модели в `alembic/env.py`, чтобы заполнить `Base.metadata`.
3. При запущенном PostgreSQL создайте и проверьте ревизию:

```sh
python -m alembic revision --autogenerate -m "describe schema change"
python -m alembic upgrade head
python -m alembic current
```

Из контейнера API (когда профиль `app` запущен):

```sh
docker compose exec backend python -m alembic upgrade head
```

Миграции выполняются явно, а не при старте приложения. Команда `upgrade head`
применяет имеющиеся ревизии. Автогенерацию всегда нужно просматривать перед применением.

## Подключение модулей

Правила описания запросов, ответов, параметров и ошибок:
[Контракты OpenAPI](docs/openapi.md).

- `src/itmo_queue/main.py` — фабрика приложения и lifespan, создающий и закрывающий
  пулы SQLAlchemy и Redis.
- `src/itmo_queue/core/config.py` — Pydantic Settings.
- `src/itmo_queue/api/router.py` — общий роутер с префиксом `/api/v1`.
- `src/itmo_queue/db/` — общая база моделей и зависимость `get_db_session`.
- `src/itmo_queue/core/redis.py` — зависимость `get_redis`.
- `alembic/` — инфраструктура миграций и начальная ревизия схемы.

Новые модули размещайте в пакете `itmo_queue` и подключайте их `APIRouter`
через `api_router.include_router(...)`. Указывайте `tags`, `summary`, `description`
и Pydantic-схемы запросов/ответов в самом модуле: FastAPI включит их в OpenAPI.
Префикс `/api/v1` уже задан общим роутером; `/health` расположен отдельно в корне.

Получайте сессию через `Depends(get_db_session)`. Зависимость выделяет сессию
на запрос и закрывает её; незавершённая транзакция откатывается.
Границы записи задавайте явно через `async with session.begin():` — успешный выход
фиксирует транзакцию, исключение откатывает её. Автоматического commit после
ответа HTTP нет. Redis-клиент через `Depends(get_redis)` общий для приложения;
его закрывает lifespan, обработчики закрывать клиент не должны.

PostgreSQL — источник истины. ITMO.ID, учебные сущности, очереди,
SSE, уведомления и TypeScript SDK остаются отдельными задачами команды.
