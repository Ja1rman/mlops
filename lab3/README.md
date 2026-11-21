# Лабораторная работа 3. Поиск по документам «Линии»

Портал ищет документы через MCP и SQLite FTS5, показывает дерево разделов и отвечает со ссылками на редакции и секции. Архив и черновики включаются фильтрами. [Корпус](data/corpus.json) содержит 36 карточек, 35 ID, 6 разделов и 12 подразделов. Данные восстановлены по фактам задания и дополнены синтетическими карточками. В [контрольных вопросах](data/control_questions.json) К01–К08 взяты из методички, К09–К16 — дополнительные сценарии.

Для запуска нужны kubectl и доступ к namespace `itmo-student-03`. Команды выполняются из корня репозитория.

```bash
export KUBECONFIG=/path/to/itmo-student-03.kubeconfig
openssl rand -hex 32 | tr -d '\n' | kubectl -n itmo-student-03 create secret generic line-mcp --from-file=MCP_TOKEN=/dev/stdin
kubectl apply -k lab3/deploy
kubectl -n itmo-student-03 rollout status deploy/llm-local
kubectl -n itmo-student-03 rollout status deploy/kb-mcp
kubectl -n itmo-student-03 rollout status deploy/portal
kubectl -n itmo-student-03 port-forward svc/portal 18080:8000
```

Секрет `line-mcp` создаётся один раз. Портал открывается на [localhost:18080](http://127.0.0.1:18080). Пример вопроса — «Почему комитет отложил расширение? Сверь цифры с правилами приёмки».

Локальная модель — `qwen2.5:1.5b-instruct-q4_K_M` в Ollama, работает на CPU. Веса хранятся на PVC `llm-local-models`. SHA-256 GGUF — `183715c435899236895da3869489cc30ac241476b4971a20285b1a462818a5b4`. Наличие модели и генерация проверяются отдельно.

```bash
kubectl -n itmo-student-03 exec deploy/llm-local -- ollama show qwen2.5:1.5b-instruct-q4_K_M
kubectl -n itmo-student-03 exec deploy/llm-local -- ollama run qwen2.5:1.5b-instruct-q4_K_M 'Ответь одним словом. Какого цвета снег?'
```

Ответ имеет маршрут `cloud`, `local` или `search_only`. Облако используется для разрешённых фрагментов. Приватный режим, метка `local_only` и ошибки облака переводят запрос на CPU. При нехватке источников или недоступности моделей портал показывает найденные выдержки.

Для llm7.io ключ записывается в Secret `llm7`. Реальный облачный маршрут пока не проверен.

```bash
read -rs llm7_token
printf '%s' "$llm7_token" | kubectl -n itmo-student-03 create secret generic llm7 \
  --from-file=LLM7_API_KEY=/dev/stdin --dry-run=client -o yaml | kubectl apply --server-side -f -
unset llm7_token
kubectl -n itmo-student-03 rollout restart deploy/portal
```

Для проверки переключения используется имитатор. Каждый проброс порта запускается в отдельном терминале.

```bash
kubectl apply -f lab3/deploy/demo.yaml
kubectl -n itmo-student-03 port-forward svc/portal-demo 18082:8000
kubectl -n itmo-student-03 port-forward svc/cloud-mock 18083:8000
```

Портал демонстрации открывается на [localhost:18082](http://127.0.0.1:18082). Ответ `429` и последующее восстановление включаются через `/control`.

```bash
curl -fsS http://127.0.0.1:18083/control -H 'Content-Type: application/json' -d '{"mode":"rate_limit","retry_after":2}'
curl -fsS http://127.0.0.1:18083/control -H 'Content-Type: application/json' -d '{"mode":"ok"}'
```

Тесты поиска, MCP и маршрутизации запускаются в [CI](../.github/workflows/line-ci.yml). Результаты проверок в кластере, К01–К08 и переключения имитатор → Ollama → имитатор сохранены в [evidence/](evidence/).
