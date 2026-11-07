# Лабораторная 2. Доставка Runbook Search API

Runbook Search API ищет по базе инженерных инструкций. Индекс объединяет TF-IDF по словам и символьным n-граммам, а результаты ранжируются по косинусной близости. Docker собирает индекс и проверяет его качество на этапе сборки. Контейнер запускается от непривилегированного пользователя и читает индекс из образа. В Kubernetes корневая файловая система контейнера доступна только для чтения.

## Локальная проверка

Запустите команды из каталога `lab2` с Python 3.12, Make и Docker Compose.

```bash
make test quality negative
make up
make smoke
curl -fsS http://127.0.0.1:18080/meta
curl -fsS http://127.0.0.1:18080/metrics
make down
```

Quality gate требует MRR ≥ 0.85 и Recall@3 ≥ 0.90. `make negative` проверяет, что обратное ранжирование формирует JSON-отчёт и завершается кодом 2. Метаданные индекса и оба отчёта записываются в `artifacts/`.

При локальной проверке обе метрики составили 0.9375 в режиме normal и 0.0 в режиме reverse. Отчёты и метаданные сохранены в [evidence/](evidence/).

Чтобы изменить порт нормального сервиса, задайте одинаковый `SEARCH_PORT` для `make up` и `make smoke`. У reverse-сервиса в Compose порт задаётся отдельно через `REVERSE_PORT` (по умолчанию 18081).

Для проверки `reverse` в контейнере соберите один образ и запустите оба режима из этого образа.

```bash
docker compose build search
docker compose --profile negative up -d --no-build --wait search bad-ranking
python3 scripts/smoke_local.py --base-url "http://127.0.0.1:${SEARCH_PORT:-18080}"
python3 scripts/smoke_local.py --base-url "http://127.0.0.1:${REVERSE_PORT:-18081}"
# Для второго запроса ожидается код завершения 2.
docker compose --profile negative down
```

Основные маршруты API — `POST /v1/search`, `GET /v1/documents/{id}/recommendations`, `GET /livez`, `GET /readyz`, `GET /meta`, `GET /metrics` и `GET /docs`.

## Результаты проверки

Прошли три теста API. Индекс `147d82cfd724` получил MRR `0.9375` и Recall@3 `0.9375`. В режиме `reverse` обе метрики равны `0.0`, quality gate завершился с кодом `2`. Сохранены [метаданные индекса](evidence/metadata.json), [отчёт normal](evidence/quality.json) и [отчёт reverse](evidence/quality-reverse.json).

В Docker Compose на учебной ВМ один и тот же образ запустился в обоих режимах. `/readyz` вернул `ready` в каждом режиме. Для контрольного запроса первым результатом был `doc-rollback` в `normal` и `doc-security` в `reverse`. Semantic smoke завершился с кодом `2` для `reverse`.

Образ, проверенный на ВМ, опубликован с тегом `v1`. Его [ссылка и digest](evidence/published-image.json) получены после `docker push`. Доступ к манифесту образа проверен без учётных данных.

## Развёртывание в учебном кластере

В namespace `mlops-students` используются Helm releases `ovsyannikov-search-staging` и `ovsyannikov-search-prod`. Для работы на учебной ВМ понадобятся выданный kubeconfig, Helm 4, kubectl, jq и Python 3.10 или новее. Манифест использует `ClusterIP` и ссылку на образ формата `repository@sha256:digest`. Пустой или некорректный digest останавливает рендеринг.

```bash
export KUBECONFIG=/path/to/mlops-students.kubeconfig
export HELM_DRIVER=configmap
kubectl auth can-i get pods -n mlops-students
helm list -n mlops-students --filter '^ovsyannikov-search-'
```

Если обе команды завершаются тайм-аутом, проверьте с преподавателем адрес API в kubeconfig и доступ к нему с учебной ВМ.

Для ручной сборки и публикации войдите в Docker Hub с правом записи в публичный репозиторий `jairman/ovsyannikov-runbook-search` и выполните команды из `lab2`.

```bash
export IMAGE_REPOSITORY=docker.io/jairman/ovsyannikov-runbook-search
export BUILD_REVISION="$(git rev-parse HEAD)"
docker login --username jairman
mkdir -p artifacts
docker buildx build --platform linux/amd64 --push \
  --tag "${IMAGE_REPOSITORY}:${BUILD_REVISION}" --metadata-file artifacts/image-build.json .
export IMAGE_DIGEST="$(jq -er '."containerimage.digest"' artifacts/image-build.json)"
```

Передайте имя своего опубликованного репозитория образов, digest и ревизию исходного кода. Перед развёртыванием скрипт выполняет `helm lint` и `helm template`. Он ждёт готовности, запускает `helm test`, проверяет образ Deployment и делает внешний запрос к API через `kubectl port-forward`. Перед изменением production скрипт заново проверяет живой staging, включая ссылку на образ, ревизию сборки из `/meta` и ожидаемый первый результат поиска.

```bash
set -o pipefail
# IMAGE_REPOSITORY, IMAGE_DIGEST и BUILD_REVISION относятся к одному образу.
./scripts/deploy_release.sh staging "$IMAGE_REPOSITORY" "$IMAGE_DIGEST" "$BUILD_REVISION" \
  2>&1 | tee staging.log

# После проверки staging и согласования promotion.
./scripts/deploy_release.sh production "$IMAGE_REPOSITORY" "$IMAGE_DIGEST" "$BUILD_REVISION" --reverse-drill \
  2>&1 | tee production.log
```

Для обычного promotion уберите флаг `--reverse-drill`. С ним скрипт сначала проверяет production с `rankingMode=normal` и сохраняет номер успешной Helm-ревизии. Затем он применяет `rankingMode=reverse` с тем же digest, проверяет readiness и ожидает неверный первый результат поиска. После проверки скрипт выполняет `helm rollback` и повторяет semantic smoke. Запускайте развёртывания последовательно.

В конце успешного promotion или проверки отката скрипт сверяет образы обоих Deployment с опубликованным digest, проверяет `/meta` и результаты поиска в обоих окружениях. Блок `Final staging and production verification` в журнале фиксирует результаты, а при учебном откате там же сохраняется история Helm.

`/readyz` подтверждает, что процесс загрузил индекс и может принимать запросы. При обратном ранжировании он отвечает HTTP 200, хотя первым возвращается нерелевантный документ. Semantic smoke проверяет, что на запрос об откате первым найден `doc-rollback`.

## GitHub Actions

Загрузите `lab1/`, `lab2/` и корневой каталог `.github/` в один публичный репозиторий с веткой `main`. GitHub читает workflow из [корня репозитория](../.github/workflows/search-delivery.yml).

1. Создайте публичный репозиторий образов `jairman/ovsyannikov-runbook-search` в Docker Hub. Добавьте repository Actions secret `DOCKERHUB_TOKEN` с токеном пользователя `jairman`, у которого есть право публиковать образы. [Документация Docker Hub](https://docs.docker.com/docker-hub/repos/manage/create/)
2. Откройте **Actions → Runbook Search delivery → Run workflow** и выберите `main`. При настроенном `DOCKERHUB_TOKEN` образ публикуется и workflow сохраняет артефакт `search-image`. Пока `KUBE_CONFIG_B64` отсутствует, staging и production показываются как **Skipped** с пояснением в сводке запуска.
3. В **Settings → Environments** создайте `production`, настройте **Required reviewers** и разрешите деплой из `main`. Перед продвижением workflow проверяет, что в истории запуска есть одобрение для `production`. [Документация GitHub](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
4. Добавьте repository Actions secret `KUBE_CONFIG_B64` со значением выданного kubeconfig в base64. Используйте конфигурацию с вложенными сертификатами и ключами, доступную с GitHub runner. Декодированная конфигурация хранится во временном файле на время шага deploy. [Документация GitHub](https://docs.github.com/en/actions/reference/security/secrets)
5. После добавления секрета начните новый запуск через **Run workflow** на `main`. Для проверки отката включите `reverse_drill`. После успешного staging подтвердите production в **Review deployments**.

Pull request выполняет тесты и обе проверки качества. Push изменений лабораторной или её workflow в `main` и ручной запуск на `main` дополнительно собирают образ `linux/amd64`. При настроенном `DOCKERHUB_TOKEN` образ публикуется с тегом SHA коммита. Каждый запуск с настроенным Kubernetes передаёт один digest в staging и production. Параллельные доставки на одной ветке сериализуются на уровне workflow.

| Артефакт Actions | Содержимое |
| --- | --- |
| `search-quality` | Метаданные индекса, отчёт normal и отчёт заблокированного reverse |
| `search-image` | Репозиторий образа, тег и digest |
| `search-staging` | Образ Deployment, Helm test и semantic smoke с `/meta` |
| `search-production` | Итоговые образы Deployment, `/meta` и semantic smoke обоих окружений, при включённом drill — история Helm и подтверждение отката |

Для сдачи приложите ссылку на публичный репозиторий и запуск workflow с этими артефактами.
