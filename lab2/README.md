# Runbook Search API

Лабораторная 2. Сервис поиска и рекомендаций по инженерным инструкциям на FastAPI и TF-IDF. Индекс входит в Docker-образ. Метаданные доступны через `/meta`, метрики — через `/metrics`.

## Локальный запуск

Python 3.12 и Docker Compose, рабочий каталог `lab2`.

```bash
make test quality negative
make up
make smoke
make down
```

Пороги качества — MRR ≥ 0.85 и Recall@3 ≥ 0.90. В режиме `normal` обе метрики составили 0.9375, в `reverse` — 0.0. Отрицательная проверка ожидает код завершения 2. Отчёты и digest опубликованного образа находятся в [evidence/](evidence/).

`/readyz` проверяет загрузку индекса. Обратное ранжирование проходит readiness, но возвращает неверный первый результат. Поэтому качество дополнительно проверяется контрольным запросом поиска.

## CI/CD

[Workflow](../.github/workflows/search-delivery.yml) запускает тесты и проверки качества на pull request, а из `main` собирает и публикует образ. Для публикации нужен Actions secret `DOCKERHUB_TOKEN`, для Kubernetes — `KUBE_CONFIG_B64` с kubeconfig в base64. Без kubeconfig развёртывание пропускается. После добавления секрета нужен новый запуск workflow.

Staging и production используют один digest. Production защищён ручным одобрением в GitHub Environment `production`. Helm работает в `itmo-student-03` с драйвером `configmap` и релизами `ovsyannikov-search-staging` и `ovsyannikov-search-prod`.

Ручной запуск с рабочим `KUBECONFIG`. Переменные образа берутся из полей `repository`, `digest` и `tag` файла `image.json` в артефакте `search-image`.

```bash
./scripts/deploy_release.sh staging "$IMAGE_REPOSITORY" "$IMAGE_DIGEST" "$BUILD_REVISION"
./scripts/deploy_release.sh production "$IMAGE_REPOSITORY" "$IMAGE_DIGEST" "$BUILD_REVISION" --reverse-drill
```

`reverse_drill` в Actions или `--reverse-drill` в скрипте проверяет ухудшение качества, откат к рабочей Helm-ревизии и восстановление `normal`. В артефактах CI сохраняются отчёты качества, digest, `/meta`, результаты поиска и история отката. В учебном кластере подтверждены одинаковый digest staging и production, успешный поиск и откат production к рабочей ревизии. [Результаты проверки](evidence/deployment.json)
