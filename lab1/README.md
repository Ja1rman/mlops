# Лабораторная работа 1. Iris в Kubernetes

`train.py` обучает `RandomForestClassifier` на наборе Iris. FastAPI загружает `model.joblib` при запуске. В Docker модель обучается при сборке образа.

## Команды для запуска

Команды выполняются из `lab1/`. Для локального запуска используется Python 3.13.

```bash
cd ml-service
uv venv --python 3.13
source .venv/bin/activate
uv pip install -r requirements.txt
python train.py
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Для проверки API нужны `curl` и `jq`. Команда выполняется из `lab1/` в отдельном терминале.

```bash
bash smoke.sh http://127.0.0.1:8000
```

Ожидаются `/live` → `alive`, `/health` → `ready`, версия `iris-v1` и классы `0`, `1`, `2` для setosa, versicolor и virginica.

## Docker

Сборка и запуск контейнера из `lab1/`.

```bash
IMAGE=docker.io/jairman/ovsyannikov-iris:v1
docker build -t "$IMAGE" ml-service
docker run --rm -p 127.0.0.1:18000:8000 "$IMAGE"
```

Контейнер проверяется через `bash smoke.sh http://127.0.0.1:18000`. При первичной настройке `docker-credential-pass` хранилище инициализируется командой `pass init "$FINGERPRINT"`, где `FINGERPRINT` — отпечаток GPG-ключа. Для публикации используется токен Docker Hub.

```bash
docker login -u jairman
docker push "$IMAGE"
```

## Kubernetes

ConfigMap, Deployment и Service `ovsyannikov-iris` размещаются в `mlops-students`. Для ручного запуска нужен рабочий `KUBECONFIG`. Service направляет запросы с порта `80` на порт контейнера `8000`. [Манифест](lab1.yaml)

```bash
kubectl apply --dry-run=client -f lab1.yaml
kubectl apply --dry-run=server -f lab1.yaml
kubectl apply -f lab1.yaml
kubectl -n mlops-students rollout status deploy/ovsyannikov-iris --timeout=180s
kubectl -n mlops-students port-forward svc/ovsyannikov-iris 18001:80
```

Проверка через Service в отдельном терминале выполняется командой `bash smoke.sh http://127.0.0.1:18001`.

## GitHub Actions

**Iris CI** собирает образ и проверяет API при push и pull request в `main` и при ручном запуске. Деплой запускается через **Actions → Iris CI → Run workflow** из `main` с параметром `deploy`. Для деплоя нужен доступ GitHub runner к API Kubernetes. Без секрета `KUBE_CONFIG_B64` развёртывание пропускается. [Workflow](../.github/workflows/iris-ci.yml)

Секрет репозитория содержит kubeconfig в base64 со встроенными сертификатами и ключами. Добавление секрета через GitHub CLI.

```bash
base64 < kubeconfig.yaml | tr -d '\n' | gh secret set KUBE_CONFIG_B64 --repo Ja1rman/mlops
```

## Результаты

На учебной ВМ модель обучена. Uvicorn и Docker-контейнер вернули `ready` и классы `0`, `1`, `2`. Манифест прошёл проверку схем через `kubeconform`. Развёртывание в учебном кластере отложено из-за тайм-аута API.
