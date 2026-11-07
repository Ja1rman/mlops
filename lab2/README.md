# Лабораторная 2. Runbook Search API

Сервис ищет по базе инженерных инструкций. Индекс TF-IDF собирается и проверяется при сборке Docker-образа. В API доступны поиск, рекомендации, метаданные `/meta` и метрики `/metrics`.

## Локальная проверка

Команды из каталога `lab2` для Python 3.12 и Docker Compose.

```bash
make test quality negative
make up
make smoke
make down
```

Прошли три теста API. Пороги качества составляют 0.85 для MRR и 0.90 для Recall@3. Обе метрики в режиме `normal` составили 0.9375, в режиме `reverse` — 0.0. Проверка отклонила `reverse` с кодом 2. [Метаданные индекса](evidence/metadata.json) · [Отчёт normal](evidence/quality.json) · [Отчёт reverse](evidence/quality-reverse.json)

Оба режима проверены в Docker Compose на учебной ВМ. `/readyz` вернул HTTP 200 в каждом режиме, хотя первым результатом запроса об откате был `doc-rollback` в `normal` и `doc-security` в `reverse`. Поэтому проверка готовности дополняется проверкой результата поиска.

Образ `docker.io/jairman/ovsyannikov-runbook-search:v1` опубликован в Docker Hub. [Ссылка и digest](evidence/published-image.json)

## CI/CD

[GitHub Actions](../.github/workflows/search-delivery.yml) проверяет тесты и качество на pull request. Запуск из `main` собирает и публикует образ с тегом SHA коммита через `DOCKERHUB_TOKEN`. Для развёртывания отдельно нужен `KUBE_CONFIG_B64` с выданным kubeconfig в base64. После добавления секрета нужен новый запуск **Run workflow** из `main`. Production требует ручного одобрения в GitHub Environment `production`.

В [запуске CI](https://github.com/Ja1rman/mlops/actions/runs/42413026661) прошли проверки и опубликован образ. В артефактах `search-quality` и `search-image` сохранены отчёты, метаданные и digest. Staging и production пропущены без kubeconfig. Проверки в учебном кластере отложены из-за тайм-аута API.

## Kubernetes и откат

Helm использует namespace `mlops-students`, драйвер `configmap` и релизы `ovsyannikov-search-staging` и `ovsyannikov-search-prod`. Workflow передаёт обоим окружениям одну ссылку на образ в формате `repository@sha256:digest`. Chart отклоняет пустой и некорректный digest.

Команды для ручного запуска из `lab2` с рабочим `KUBECONFIG`. Для `IMAGE_REPOSITORY`, `IMAGE_DIGEST` и `BUILD_REVISION` используются поля `repository`, `digest` и `tag` из `image.json` артефакта `search-image`.

```bash
./scripts/deploy_release.sh staging "$IMAGE_REPOSITORY" "$IMAGE_DIGEST" "$BUILD_REVISION"
./scripts/deploy_release.sh production "$IMAGE_REPOSITORY" "$IMAGE_DIGEST" "$BUILD_REVISION"
```

На staging выполняется `helm test`. Проверка каждого окружения сохраняет образ Deployment, `/meta` и результат поиска. Режим `reverse_drill` в Actions или флаг `--reverse-drill` в скрипте переключает production на обратное ранжирование с тем же digest, выполняет `helm rollback` и проверяет восстановление `normal`. Результаты и история Helm сохраняются в журналах запуска.
