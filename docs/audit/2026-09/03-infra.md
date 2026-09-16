# Аудит dev → main · 1.3 Инфра

Дата: 2026-09-16. Три файла, 53 строки. Перенесено в `main` дословно (`docker-compose.yml`, `.github/workflows/deploy-production.yml`); `Makefile` в dev отличается только отсутствием моего `load-hot` — переносить нечего.

## 1. Что в dev

| Файл | Что | Зачем |
|---|---|---|
| `docker-compose.yml` | Рекомендеру: `RECOMMENDER_HUBNESS_METHOD` (по умолчанию `csls`), `DIRECT_IMAGE_*_PATH` (по умолчанию пусто = photo-канал выключен), volume `${ARTIFACTS_HOST_DIR:-./artifacts_external}:/app/artifacts_external:ro`. | Без выключателя hubness рекомендер на 58k мест считает ~85 с/запрос (шаг «каждое место с каждым», квадратичный). Модель на 58k — на диске сервера, не в образе. |
| `deploy-production.yml` | В `.env` на сервере: `MAP_TILE_VERSION=3` (было 1), `ARTIFACTS_HOST_DIR=/opt/backend_sloco/artifacts_external`, для v4 — пути в `artifacts_external/*_all_themes*`, `RECOMMENDER_WEIGHTS_PRESET=text_direct`, `RECOMMENDER_HUBNESS_METHOD=none`, photo-артефакт на 81 967 мест. Плюс: протухший `GHCR_READ_TOKEN` больше не валит деплой — предупреждение и анонимный pull. | `MAP_TILE_VERSION` сбрасывает 7-дневный кеш тайлов в Redis и `?v=` у клиента — иначе карта показывала бы тайлы без Берлина. GHCR: 2026-08-28 деплой упал на `docker login` при публичных образах. |

Деплой — ручной `workflow_dispatch` с полем `ref` (по умолчанию `main`). Кирилл деплоил с `ref: dev`. «Переключить прод на main» = выбрать `main` при следующем деплое, файлы менять не надо.

## 2. Что важно знать владельцу

- **Образы `ghcr.io/maevskyy/gateway_sloco` и `recommender_sloco` — публичные.** На этом и держится обход протухшего токена. Образ gateway — код без секретов (`.env` рендерится на сервере), образ рекомендера несёт ~190 MB артефактов из репо. Решить осознанно: оставить публичными (тогда токен не нужен вовсе) или закрыть (тогда токен обязателен и его надо ротировать по расписанию — он уже протухал). Ротация токенов вообще не заведена — завести.
- **Модель рекомендера доставляется на сервер `rsync` руками** в `/opt/backend_sloco/artifacts_external`. В репо нет ни списка файлов, ни контрольных сумм, ни скрипта. Если папку потерять — рекомендер не поднимется. Это SLO-19.
- **На хосте живут вещи вне репо:** cron экспортёра event log (03:15 UTC, удаляет данные старше 30 дней — `02-recommender.md` §2), `sloco-dashboard` (SLO-29), папка `artifacts_external`, папка `exports` с parquet — единственная копия event log. Нужен один документ «что стоит на сервере руками» и бэкап `exports`.
- `MAP_TILE_VERSION` в `services/gateway/.env.example` по-прежнему `1`, в проде `3`. Не баг, но при локальном запуске с Redis, набитым старыми тайлами, будет путать.
- Наблюдаемость (`--profile observability`, Grafana/Loki/Prometheus) в dev не менялась.

## 3. Итог

`main` теперь деплоится: compose и workflow совпадают с тем, что на проде. Блокер снят. Ротацию токенов и решение про публичность образов — в Linear.
