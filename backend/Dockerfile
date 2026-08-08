FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential freetds-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY src ./src

# uv.lock, not this Dockerfile, is the single source of truth for exact (including
# transitive) dependency versions -- exporting it to a requirements file keeps the
# image build reproducible across machines instead of re-resolving on every build.
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o requirements.txt \
    && python -m pip wheel --wheel-dir /wheels -r requirements.txt .

FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    PLATFORM_CATALOG_PATH=/app/config/data_assets.yaml \
    PLATFORM_SCHEMA_REGISTRY_PATH=/app/config/schema_registry.yaml \
    PLATFORM_RETURN_CONFIGURATION_PATH=/app/config/returns/production.yaml \
    PLATFORM_DEPENDENCY_SIMULATION_CONFIGURATION_PATH=/app/config/dependency_simulation.yaml \
    PLATFORM_AI_GATEWAY_CONFIGURATION_PATH=/app/config/ai_gateway.yaml

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
