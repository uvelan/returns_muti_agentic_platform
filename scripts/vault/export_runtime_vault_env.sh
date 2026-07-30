#!/usr/bin/env bash
# Source this file from Linux host process launchers.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PLATFORM_VAULT_ENABLED="true"
export PLATFORM_VAULT_ADDRESS="${PLATFORM_VAULT_ADDRESS:-http://127.0.0.1:8200}"
export PLATFORM_VAULT_TOKEN_FILE="${PLATFORM_VAULT_TOKEN_FILE:-$ROOT/.vault-local/return-platform.token}"
export PLATFORM_MONGO_DSN_SECRET_REFERENCE="vault://secret/production/data-sources/mongodb#dsn"
export PLATFORM_SOURCE_MONGO_DSN_SECRET_REFERENCE="vault://secret/production/data-sources/mongodb#source_dsn"
export PLATFORM_NEO4J_PASSWORD_SECRET_REFERENCE="vault://secret/production/data-sources/neo4j#password"
export PLATFORM_VALKEY_PASSWORD_SECRET_REFERENCE="vault://secret/production/data-sources/valkey#password"
export PLATFORM_SQLSERVER_PASSWORD_SECRET_REFERENCE="vault://secret/production/data-sources/sqlserver#password"
export PLATFORM_VALIDATION_FINGERPRINT_KEY_SECRET_REFERENCE="vault://secret/production/platform/validation#fingerprint_key"

# AI credential references are published only after provider/model validation.
# Local bootstrap-managed keys are discovered at their fixed Vault paths, so raw
# key values never need to be exported to an application process.
