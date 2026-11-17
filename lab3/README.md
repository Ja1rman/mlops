# Лабораторная работа 3. Поиск по документам «Линии»

Портал ищет до пяти редакций, показывает дерево разделов и отвечает на вопросы со ссылками вида `/docs/M-05/2.0#s01`. Поиск выполняет отдельный MCP-сервис с SQLite FTS5. В выдаче по умолчанию действующие редакции, архив и черновики доступны через фильтры.

[Учебный корпус](data/README.md) содержит 36 записей, 35 ID, 6 разделов и 12 подразделов. Отдельный корпус в выданных файлах отсутствовал, поэтому записи восстановлены по фактам из задания и дополнены синтетическими карточками.

## Команды для запуска

Требуется доступ к namespace `itmo-student-03`. Все сервисы используют `ClusterIP`. Ollama работает без GPU с моделью `qwen2.5:1.5b-instruct-q4_K_M` и сохраняет веса на `2Gi` PVC `llm-local-models`. Выбрана компактная мультиязычная модель для CPU и квоты учебного namespace.

Из кластера загрузка с `registry.ollama.ai` завершалась тайм-аутом. Веса загружены с `ollama.com` на учебной ВМ, проверены по SHA-256 и записаны на PVC. После пересоздания Pod с тем же PVC Ollama использует готовые файлы. SHA-256 файла GGUF: `183715c435899236895da3869489cc30ac241476b4971a20285b1a462818a5b4`.

Для самостоятельного развёртывания из корня репозитория сначала создаётся внутренний секрет MCP. Если секрет уже существует, команда создания не нужна.

```bash
export KUBECONFIG=/path/to/itmo-student-03.kubeconfig
openssl rand -hex 32 | tr -d '\n' | kubectl -n itmo-student-03 create secret generic line-mcp --from-file=MCP_TOKEN=/dev/stdin
kubectl apply -k lab3/deploy
kubectl -n itmo-student-03 get deploy,pvc
kubectl -n itmo-student-03 port-forward svc/portal 18080:8000
```

Интерфейс доступен на [localhost:18080](http://127.0.0.1:18080). Пример вопроса — «Почему комитет отложил расширение? Сверь цифры с правилами приёмки».

## Маршруты ответа

- `cloud` — llm7.io для разрешённых фрагментов. Инструкция модели отделена от текста документов.
- `local` — Ollama для приватных запросов, при отсутствии облачного ключа или после ошибки облака. Модель выбирает источники, а портал показывает точные выдержки из тех же отобранных фрагментов.
- `search_only` — найденные документы при недостатке подтверждений или если модель не смогла дать ответ со ссылками.

Для реального облака нужен бесплатный ключ из [личного кабинета llm7.io](https://dash.llm7.io/). Команды для записи ключа в Kubernetes Secret и перезапуска портала:

```bash
printf 'LLM7 token: '; IFS= read -rs llm7_token; printf '\n'
printf '%s' "$llm7_token" | kubectl -n itmo-student-03 create secret generic llm7 \
  --from-file=LLM7_API_KEY=/dev/stdin --dry-run=client -o yaml | kubectl apply --server-side -f -
unset llm7_token
kubectl -n itmo-student-03 rollout restart deploy/portal
kubectl -n itmo-student-03 rollout status deploy/portal
```

## Проверки

[Line RAG CI](../.github/workflows/line-ci.yml) запускает тесты поиска и маршрутизации, проверяет манифесты, собирает образы и проверяет MCP и портал в Docker. Токены провайдера и kubeconfig для CI не требуются.

В кластере готовы [пять Deployment и PVC](evidence/cluster.json). Проверены независимый [MCP-клиент](evidence/mcp.json), [16 поисковых сценариев](evidence/search.json) и [генерация Ollama](evidence/ollama.json). При серии запросов `kubectl top pod` показал около `1,5 CPU` и `1152 MiB` у Pod модели. До первого запроса — `3m CPU` и `10 MiB`.

Независимая проверка `tools/list` и `tools/call` использует `python -m kb.smoke http://127.0.0.1:18001/mcp` из `lab3`, зависимости `kb/requirements.txt` и токен `MCP_TOKEN`. Для доступа нужен локальный проброс `kubectl -n itmo-student-03 port-forward svc/kb-mcp 18001:8000`.

Имитатор разворачивается через `kubectl apply -f lab3/deploy/demo.yaml`. Портал демонстрации доступен через `port-forward svc/portal-demo 18082:8000`, управление имитатором — через `port-forward svc/cloud-mock 18083:8000`. Команда `curl -fsS http://127.0.0.1:18083/control -H 'Content-Type: application/json' -d '{"mode":"rate_limit","retry_after":2}'` включает `429`. Проверено переключение [имитатор → Ollama → имитатор](evidence/routing.json) с сохранением ссылки на документ. Реальный маршрут llm7.io требует ключа.
