# SokolykOff Shop

[![CI](https://github.com/SokolykOff/sokolykoff-shop/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SokolykOff/sokolykoff-shop/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB)
![Django 5.2](https://img.shields.io/badge/Django-5.2-092E20)
![PostgreSQL 17](https://img.shields.io/badge/PostgreSQL-17-4169E1)
![Celery + Redis](https://img.shields.io/badge/Celery-Redis%207-DC382D)
![Vue 3](https://img.shields.io/badge/Vue-3-4FC08D)
![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED)

Интернет-магазин электроники на Django и PostgreSQL с каталогом, корзиной и оформлением заказов. Основной сценарий проекта — резервирование остатков, когда несколько покупателей одновременно заказывают один товар. Оплата симулируется задачей Celery, витрина работает на Vue 3 в шаблонах Django.

![Архитектура SokolykOff Shop](docs/architecture.svg)

## Архитектура

| Компонент | Назначение |
| --- | --- |
| Django | JSON API каталога, корзины, заказов и профиля. Схема OpenAPI описана в `backend/mysite/api_docs.py` |
| PostgreSQL | Каталог, заказы, пользователи и сессии. Транзакции, блокировки строк и ограничения целостности |
| Celery и Redis | Redis передаёт задачи и хранит их результаты. Celery выполняет симуляцию оплаты и обновляет заказ |
| Vue 3 и Django Templates | Витрина, корзина, профиль и виджет консультанта |
| Gunicorn и WhiteNoise | Запуск веб-приложения и раздача собранной статики |

Поиск извлекает бюджет из запроса, исправляет раскладку и опечатки, подбирает товары из каталога. LLM объясняет выбор и формирует сводки отзывов. Если ключ не задан или провайдер недоступен, приложение формирует ответ локально.

## Заказы и целостность данных

| Сценарий | Реализация |
| --- | --- |
| Два покупателя заказывают последний товар | `select_for_update()` блокирует строки товаров до конца транзакции. Остаток проверяется под блокировкой. Если товара уже недостаточно, API возвращает `409` |
| Корзины содержат одни и те же товары | Строки блокируются в порядке ID. Единый порядок снижает риск дедлоков |
| Заказ не удалось оформить | Создание заказа и изменение остатков выполняются в одной транзакции |
| Отмена пришла повторно | Сервис блокирует строку заказа и проверяет статус перед возвратом остатков |
| Задача оплаты пришла повторно | Состояние заказа проверяется под блокировкой. Повторная обработка оплаченного заказа не увеличивает счётчик покупок |
| Данные изменили напрямую в БД | Ограничения PostgreSQL защищают от отрицательных остатков и сумм, недопустимых цен и количества, а также дублей товара в заказе |

Код резервирования и отмены находится в [`backend/orders/services.py`](backend/orders/services.py), задача оплаты — в [`backend/orders/tasks.py`](backend/orders/tasks.py).

## Запуск

Нужны Docker и Docker Compose v2. Из корня репозитория скопируйте `backend/.env.example` в `backend/.env`. В PowerShell можно выполнить `Copy-Item backend/.env.example backend/.env`.

```
docker compose up -d --build --wait --wait-timeout 180
docker compose exec web python manage.py loaddata fixtures.json
```

Сервис `migrate` применяет миграции, а `media-init` подготавливает общий том для медиафайлов. Фикстуру загружайте один раз в пустую демонстрационную базу. Для проверки оплаты используйте тестовые данные.

- Витрина — [localhost:8000](http://localhost:8000/)
- Админка — [localhost:8000/admin/](http://localhost:8000/admin/)
- Swagger UI — [localhost:8000/api/docs/](http://localhost:8000/api/docs/)
- Проверка доступности — [localhost:8000/api/health/](http://localhost:8000/api/health/)

Логи Django и Celery доступны через `docker compose logs -f web celery`. Команда `docker compose down` останавливает сервисы и сохраняет данные в томах.

## API

| Метод | Маршрут | Назначение |
| --- | --- | --- |
| GET | `/api/catalog/` | Каталог с фильтрами, поиском и пагинацией |
| GET | `/api/product/{id}/` | Карточка товара |
| GET, POST | `/api/product/{id}/reviews/` | Просмотр и добавление отзывов |
| GET, POST, DELETE | `/api/basket/` | Сессионная корзина |
| GET, POST | `/api/orders/` | Список и создание заказов |
| GET, POST | `/api/order/{id}/` | Просмотр и обновление заказа |
| POST | `/api/payment/{id}/` | Запуск симуляции оплаты |
| POST | `/api/sign-up/`, `/api/sign-in/`, `/api/sign-out/` | Регистрация, вход и выход |
| GET, POST | `/api/profile/` | Просмотр и обновление профиля |
| POST | `/api/ai-assistant/` | Подбор товаров по запросу и бюджету |
| GET | `/api/health/` | Проверка доступности приложения и БД |

Схема OpenAPI доступна по `/api/schema/`.

## Тесты и CI

| Проверка | Что проверяет |
| --- | --- |
| `test` | Миграции, загрузку фикстур, сборку статики и тесты Django на PostgreSQL 17 |
| `docker-smoke` | Сборку образов, запуск Compose, HTTP-ответы и выполнение задачи через Redis и отдельный процесс Celery |

Автотесты покрывают конкурентное резервирование остатков, блокировку отдельных строк, ограничения БД, повторную отмену и оплату, авторизацию, поиск и локальный ответ при сбое LLM.

Чтобы запустить полный набор тестов в поднятом Compose:

```
docker compose exec -e CELERY_TASK_ALWAYS_EAGER=True web python manage.py test --noinput
```

При запуске вне Docker устанавливайте зависимости из каталога `backend` командой `python -m pip install -r requirements-lock.txt`. Укажите в `backend/.env` параметры доступного PostgreSQL. Для подключения с хоста к контейнеру используйте `POSTGRES_HOST=127.0.0.1`. Полный набор тестов запускается из `backend` командой `python manage.py test --noinput`.