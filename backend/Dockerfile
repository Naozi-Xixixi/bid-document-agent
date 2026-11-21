FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY backend/requirements.txt backend/requirements-qdrant.txt /tmp/requirements/
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements/requirements.txt -r /tmp/requirements/requirements-qdrant.txt

COPY --chown=app:app backend/app /app/app
COPY --chown=app:app backend/clients /app/clients
RUN mkdir -p /app/data && chown app:app /app/data

USER app
EXPOSE 3896
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3896"]
