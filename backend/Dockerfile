# An extra root CA, as PEM text, for networks that terminate TLS in the middle.
# Without it every package download in this file fails with
# `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` -- the
# container trusts nothing the corporate proxy signs, even where the host does.
#
#   docker build --build-arg EXTRA_CA_CERTS="$(cat corp-root.pem)" backend
#   EXTRA_CA_CERTS="$(cat corp-root.pem)" docker compose build backend
#
# A build arg rather than a secret mount, because it works with plain
# `docker build` and with compose, and a root CA certificate is public by
# design -- it is the private key that is secret, and that is not this.
# Empty by default: on an unintercepted network nothing changes.
ARG EXTRA_CA_CERTS=""

FROM python:3.13-slim AS builder
ARG EXTRA_CA_CERTS

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_NO_INTERACTION=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential freetds-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# `update-ca-certificates` alone is not enough: pip ships its own trust store
# (certifi) and ignores the system one unless pointed at it, which is why
# PIP_CERT is set too. SSL_CERT_FILE covers Poetry's own HTTP client.
RUN if [ -n "${EXTRA_CA_CERTS}" ]; then \
      printf '%s\n' "${EXTRA_CA_CERTS}" > /usr/local/share/ca-certificates/extra-ca.crt \
      && update-ca-certificates; \
    fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# `poetry-plugin-export` alongside Poetry: `poetry export` ships as a plugin in
# Poetry 2.x rather than a built-in, so installing Poetry alone leaves the
# command missing and the build fails at the export line rather than here.
RUN python -m pip install "poetry==${POETRY_VERSION}" poetry-plugin-export

WORKDIR /build
COPY pyproject.toml poetry.lock ./
COPY src ./src

# poetry.lock, not this Dockerfile, is the single source of truth for exact
# (including transitive) dependency versions -- exporting it to a requirements
# file keeps the image build reproducible across machines instead of
# re-resolving on every build.
RUN poetry export --without-hashes --only main -f requirements.txt -o requirements.txt \
    && python -m pip wheel --wheel-dir /wheels -r requirements.txt .

# The real-infra test image. A separate stage on purpose: the runtime image
# below carries only main dependencies and must never carry pytest, and this one
# never copies application source -- `compose.yaml`'s `diagnostics` service
# bind-mounts the working tree over /workspace_root/backend instead.
#
# It exists because `run_real_infra_suite.sh` used to target a container that
# nothing in this repository created: a hand-made python:3.13-slim that source
# was `docker cp`-ed into. Because `docker cp` merges rather than replaces, a
# file left behind by one session once produced a green run for code that was
# not in the tree. A bind mount cannot do that -- it is the tree, exactly.
FROM python:3.13-slim AS test
ARG EXTRA_CA_CERTS

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_NO_INTERACTION=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/workspace_root/backend/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential freetds-dev libsybdb5 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# See the ARG at the top of this file. Repeated because each stage starts from a
# fresh base and inherits nothing from the one before it.
RUN if [ -n "${EXTRA_CA_CERTS}" ]; then \
      printf '%s\n' "${EXTRA_CA_CERTS}" > /usr/local/share/ca-certificates/extra-ca.crt \
      && update-ca-certificates; \
    fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

RUN python -m pip install "poetry==${POETRY_VERSION}"

WORKDIR /workspace_root/backend
COPY pyproject.toml poetry.lock ./
# The dev group is exactly what this image is for, so unlike the builder stage
# above it is not excluded. `--no-root` keeps the project itself out: it arrives
# on PYTHONPATH from the bind mount, so the code under test is always the
# working tree and never a copy installed at image build time.
#
# `POETRY_VIRTUALENVS_CREATE=false` with /opt/venv already on PATH makes Poetry
# install into that venv rather than managing one of its own somewhere else,
# which is what `run_real_infra_suite.sh` invokes by absolute path.
RUN python -m venv "${VIRTUAL_ENV}" \
    && POETRY_VIRTUALENVS_CREATE=false poetry install --with dev --no-root

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

# Wheels only: the runtime image never installs Poetry, never reaches the
# network, and never carries a compiler.
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels return-platform-backend \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=app:app config ./config
COPY --chown=app:app scripts ./scripts
USER app

EXPOSE 8000
CMD ["uvicorn", "return_platform.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
