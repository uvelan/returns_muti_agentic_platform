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

# The real-infra test image. A separate stage on purpose: the runtime image
# below is built `--no-dev` and must never carry pytest, and this one never
# copies application source -- `compose.yaml`'s `diagnostics` service
# bind-mounts the working tree over /workspace_root/backend instead.
#
# It exists because `run_real_infra_suite.sh` used to target a container that
# nothing in this repository created: a hand-made python:3.13-slim that source
# was `docker cp`-ed into. Because `docker cp` merges rather than replaces, a
# file left behind by one session once produced a green run for code that was
# not in the tree. A bind mount cannot do that -- it is the tree, exactly.
FROM python:3.13-slim AS test

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PYTHONPATH=/workspace_root/backend/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential freetds-dev libsybdb5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /workspace_root/backend
COPY pyproject.toml uv.lock ./
# No `--no-dev` here, unlike the builder stage above -- the dev group is exactly
# what this image is for. `--no-emit-project` keeps the project itself out: it
# arrives on PYTHONPATH from the bind mount, so the code under test is always
# the working tree and never a wheel built at image time.
RUN uv venv "${VIRTUAL_ENV}" \
    && uv export --frozen --no-emit-project --no-hashes -o /tmp/requirements-dev.txt \
    && uv pip install --python "${VIRTUAL_ENV}/bin/python" -r /tmp/requirements-dev.txt \
    && rm /tmp/requirements-dev.txt

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
