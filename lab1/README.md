# Лабораторная работа 1. Iris в Kubernetes

`train.py` обучает `RandomForestClassifier` с 50 деревьями и `random_state=42` на всём наборе Iris. API загружает `model.joblib` при запуске. При сборке образа обучение выполняется внутри Docker.

## Обучение и локальный запуск

Команды выполняются из `lab1/`. Для локального запуска нужен Python 3.12 или 3.13.

```bash
cd ml-service
uv venv --python 3.13
source .venv/bin/activate
uv pip install -r requirements.txt
python train.py
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Если `uv` недоступен, создайте окружение командой `python3.13 -m venv .venv` и после активации установите зависимости через `python -m pip install -r requirements.txt`. С Python 3.12 используйте `python3.12 -m venv .venv`.

## Проверка API

В другом терминале выполните команды ниже. Для проверки через `port-forward` укажите порт `18001` в `BASE_URL`.

```bash
BASE_URL=http://127.0.0.1:8000
curl -fsS "$BASE_URL/live"
printf '\n'
curl -fsS "$BASE_URL/health"
printf '\n'

for features in '[5.1,3.5,1.4,0.2]' '[7.0,3.2,4.7,1.4]' '[6.3,3.3,6.0,2.5]'; do
  curl -fsS "$BASE_URL/predict" \
    -H 'Content-Type: application/json' \
    -d "{\"features\":$features}"
  printf '\n'
done
```

`/live` возвращает `{"status":"alive"}`, `/health` возвращает `{"status":"ready","model_version":"iris-v1"}`. Ожидаемые `class_id` в порядке запросов — `0` (setosa), `1` (versicolor), `2` (virginica). Все ответы `/predict` содержат `model_version`.

## Docker-образ

Остановите локальный Uvicorn перед запуском контейнера на том же порту. Выполните из `lab1/`.

```bash
export DOCKERHUB_USER=jairman
export IMAGE="docker.io/${DOCKERHUB_USER}/ovsyannikov-iris:v1"
docker build -t "$IMAGE" ml-service
docker run --rm -p 127.0.0.1:8000:8000 "$IMAGE"
```

Проверьте API, затем остановите контейнер. На учебной ВМ с настроенным `docker-credential-pass` инициализируйте новое хранилище `pass` отпечатком своего GPG-ключа. Если ключа ещё нет, создайте его. После `read` вставьте отпечаток из вывода `gpg`.

```bash
KEY_ID="docker@$(hostname)"
gpg --batch --passphrase '' --quick-generate-key "$KEY_ID" default default never
gpg --list-secret-keys --fingerprint "$KEY_ID"
read -r FINGERPRINT
pass init "$FINGERPRINT"
```

Создайте публичный репозиторий `ovsyannikov-iris` на Docker Hub. Введите токен доступа в запросе пароля `docker login`, затем отправьте образ.

```bash
docker login -u "$DOCKERHUB_USER"
docker push "$IMAGE"
```

## Развёртывание

Манифест использует образ `docker.io/jairman/ovsyannikov-iris:v1`. ConfigMap, Deployment и Service имеют имя `ovsyannikov-iris` в общем namespace `mlops-students`. ConfigMap задаёт путь `/app/model.joblib` и версию `iris-v1`. Service направляет запросы с порта 80 на порт контейнера 8000. [Манифест](lab1.yaml)

Deployment импортирует настройки через `envFrom`. Readiness-проба проверяет `/health`, а пробы запуска и жизнеспособности проверяют `/live`. Селекторы Deployment и Service совпадают с метками Pod.

```bash
kubectl config current-context
kubectl -n mlops-students get cm/ovsyannikov-iris deploy/ovsyannikov-iris \
  svc/ovsyannikov-iris --ignore-not-found
kubectl apply --dry-run=client -f lab1.yaml
kubectl apply --dry-run=server -f lab1.yaml
kubectl apply -f lab1.yaml
kubectl -n mlops-students rollout status deployment/ovsyannikov-iris --timeout=180s
kubectl -n mlops-students get cm,deploy,svc,pods \
  -l app.kubernetes.io/instance=ovsyannikov-iris
kubectl -n mlops-students port-forward svc/ovsyannikov-iris 18001:80
```

В отдельном терминале на той же машине повторите проверку API с `BASE_URL=http://127.0.0.1:18001`.

## GitHub Actions

Workflow **Iris CI** собирает образ и проверяет API в Docker при изменениях лабораторной в `main`, в pull request в `main` и при ручном запуске. Для этих проверок достаточно файлов репозитория. [Workflow](../.github/workflows/iris-ci.yml)

Когда появится доступ к кластеру, добавьте `KUBE_CONFIG_B64` в **Settings → Secrets and variables → Actions → New repository secret**. Значение секрета — kubeconfig в base64 со встроенными сертификатами и ключами. API Kubernetes должен быть доступен с GitHub runner, а учётная запись в kubeconfig должна иметь доступ к ресурсам лабораторной в `mlops-students`. Установить секрет через GitHub CLI можно из файла `kubeconfig.yaml`.

```bash
base64 < kubeconfig.yaml | tr -d '\n' | gh secret set KUBE_CONFIG_B64 --repo Ja1rman/mlops
```

Откройте **Actions → Iris CI → Run workflow**, выберите `main` и включите `deploy`. После проверки контейнера workflow выполнит серверный dry-run, применит манифест с образом `docker.io/jairman/ovsyannikov-iris:v1`, дождётся Deployment и проверит `/live`, `/health` и все три предсказания через Service. При пустом секрете workflow сообщит о пропуске развёртывания.

## Результаты проверки

На учебной ВМ `train.py` создал модель, а Uvicorn и Docker-контейнер вернули `ready` и классы `0`, `1`, `2` на контрольных примерах. Контейнер прошёл проверку с UID `10001`, корневой файловой системой только для чтения, лимитом `256 MiB` и `0,5 CPU`. Три ресурса из манифеста прошли проверку схем Kubernetes через `kubeconform`.
