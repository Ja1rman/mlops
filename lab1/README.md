# Лабораторная работа 1. Iris в Kubernetes

Классификатор Iris с API на FastAPI. `train.py` обучает Random Forest и сохраняет `model.joblib`. Docker-образ включает обученную модель.

## Локальный запуск

Команды выполняются из `lab1/`.

```bash
cd ml-service
uv venv --python 3.13
source .venv/bin/activate
uv pip install -r requirements.txt
python train.py
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Проверка из `lab1/` в другом терминале, нужны `curl` и `jq`.

```bash
bash smoke.sh http://127.0.0.1:8000
```

API предоставляет `/live`, `/health` и `/predict`. Проверка ожидает классы `0`, `1`, `2` для setosa, versicolor и virginica.

## Docker

```bash
IMAGE=docker.io/jairman/ovsyannikov-iris:v1
docker build -t "$IMAGE" ml-service
docker run --rm -p 127.0.0.1:18000:8000 "$IMAGE"
```

Проверка контейнера — `bash smoke.sh http://127.0.0.1:18000`.

## Kubernetes

Манифест создаёт ConfigMap, Deployment и Service `ovsyannikov-iris` в `mlops-students`. Нужен рабочий `KUBECONFIG`. [Манифест](lab1.yaml)

```bash
kubectl apply --dry-run=client -f lab1.yaml
kubectl apply --dry-run=server -f lab1.yaml
kubectl apply -f lab1.yaml
kubectl -n mlops-students rollout status deploy/ovsyannikov-iris --timeout=180s
kubectl -n mlops-students port-forward svc/ovsyannikov-iris 18001:80
```

Проверка через Service — `bash smoke.sh http://127.0.0.1:18001` в другом терминале.

## GitHub Actions

**Iris CI** собирает образ и проверяет API при push и pull request в `main`. Ручной запуск с параметром `deploy` развёртывает сервис в Kubernetes, если задан секрет `KUBE_CONFIG_B64` с kubeconfig в base64. [Workflow](../.github/workflows/iris-ci.yml)
