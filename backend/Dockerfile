FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential freetds-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends libsybdb5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels return-platform-backend \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=app:app config ./config
COPY --chown=app:app scripts ./scripts
USER app

EXPOSE 8000
CMD ["uvicorn", "return_platform.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
