FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt
COPY app app
COPY data data
COPY scripts scripts
COPY tests tests
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && python scripts/build_index.py --output artifacts \
    && pytest -q \
    && python scripts/evaluate.py --artifact-dir artifacts

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ARTIFACT_DIR=/app/artifacts

RUN addgroup --system app && adduser --system --ingroup app --uid 10001 app
WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels
COPY --from=builder --chown=10001:app /build/app app
COPY --from=builder --chown=10001:app /build/artifacts artifacts

USER 10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
