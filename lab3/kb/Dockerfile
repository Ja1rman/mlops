FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 KB_HOST=0.0.0.0
WORKDIR /app

COPY kb/requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY --chown=10001:10001 kb/__init__.py kb/index.py kb/search.py kb/server.py kb/
COPY --chown=10001:10001 data/corpus.json data/corpus.json
RUN python -m kb.index data/corpus.json index.sqlite && chmod 0444 index.sqlite

USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "kb.server"]
