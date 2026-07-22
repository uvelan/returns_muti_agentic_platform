#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
DOCUMENT_ID="${DOCUMENT_ID:-CUSTOMER_GRAPH_SANDBOX:d084d10c-5bdf-4002-befb-8ccb9948f9e7}"
SYNC_RUN_ID="${SYNC_RUN_ID:-d084d10c-5bdf-4002-befb-8ccb9948f9e7}"
REPORT_DIGEST="${REPORT_DIGEST:-75b63cf87a1742e93dd05eb2542d6bfe17f3b345ffe3542d73fac32d664b33c8}"
EXPECTED_DOCUMENT_DIGEST="${EXPECTED_DOCUMENT_DIGEST:-6ce23e2568171b3f53827dfb8b822f4c4cd2cec60080a6c959326136bdb81f5b}"
EVIDENCE_DIRECTORY="${EVIDENCE_DIRECTORY:-docs/evidence/graph_evidence_api}"
CURL_CONNECT_TIMEOUT_SECONDS="${CURL_CONNECT_TIMEOUT_SECONDS:-3}"
CURL_MAX_TIME_SECONDS="${CURL_MAX_TIME_SECONDS:-10}"

mkdir -p "${EVIDENCE_DIRECTORY}"

request_endpoint() {
  local name="$1"
  local path="$2"
  local body_file="${EVIDENCE_DIRECTORY}/${name}.json"
  local header_file="${EVIDENCE_DIRECTORY}/${name}.headers"
  local status_file="${EVIDENCE_DIRECTORY}/${name}.status"

  local http_status
  http_status="$(
    curl \
      --silent \
      --show-error \
      --connect-timeout "${CURL_CONNECT_TIMEOUT_SECONDS}" \
      --max-time "${CURL_MAX_TIME_SECONDS}" \
      --request GET \
      --header "Accept: application/json" \
      --dump-header "${header_file}" \
      --output "${body_file}" \
      --write-out "%{http_code}" \
      "${BASE_URL}${path}"
  )"

  printf '%s\n' "${http_status}" > "${status_file}"

  if [[ "${http_status}" != "200" ]]; then
    printf 'API validation failed: %s returned HTTP %s\n' \
      "${name}" "${http_status}" >&2
    cat "${body_file}" >&2 || true

    if [[ "${http_status}" == "403" && "${name}" == "document_full" ]]; then
      printf '%s\n' \
        "The development principal lacks console_admin. Do not weaken the API role boundary." \
        >&2
    fi
    exit 4
  fi
}

request_endpoint \
  "list" \
  "/data-console/v1/graph-evidence?page_size=25"

request_endpoint \
  "latest" \
  "/data-console/v1/graph-evidence/validation/latest"

request_endpoint \
  "document_summary" \
  "/data-console/v1/graph-evidence/documents/${DOCUMENT_ID}"

request_endpoint \
  "document_full" \
  "/data-console/v1/graph-evidence/documents/${DOCUMENT_ID}/full"

request_endpoint \
  "sync_run" \
  "/data-console/v1/graph-evidence/sync-runs/${SYNC_RUN_ID}"

request_endpoint \
  "report_digest" \
  "/data-console/v1/graph-evidence/reports/${REPORT_DIGEST}"

export BASE_URL
export DOCUMENT_ID
export SYNC_RUN_ID
export REPORT_DIGEST
export EXPECTED_DOCUMENT_DIGEST
export EVIDENCE_DIRECTORY

poetry run python - <<'PY'
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

evidence_directory = Path(os.environ["EVIDENCE_DIRECTORY"])
expected_document_id = os.environ["DOCUMENT_ID"]
expected_sync_run_id = os.environ["SYNC_RUN_ID"]
expected_report_digest = os.environ["REPORT_DIGEST"]
expected_document_digest = os.environ["EXPECTED_DOCUMENT_DIGEST"]

response_names = (
    "list",
    "latest",
    "document_summary",
    "document_full",
    "sync_run",
    "report_digest",
)

forbidden_fragments = (
    "mongo_dsn",
    "neo4j_password",
    "sqlserver_password",
    "valkey_password",
    "mssql_sa_password",
    "mongo_root_password",
    "graph_password",
    "temporal_db_password",
)


def fail(message: str) -> None:
    raise SystemExit(message)


def load_response(name: str) -> tuple[dict[str, Any], bytes]:
    path = evidence_directory / f"{name}.json"
    raw = path.read_bytes()
    lowered = raw.decode("utf-8", errors="replace").lower()
    for fragment in forbidden_fragments:
        if fragment in lowered:
            fail(f"{name}: forbidden secret/configuration fragment found: {fragment}")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        fail(f"{name}: response is not valid JSON: {error}")

    if not isinstance(payload, dict):
        fail(f"{name}: top-level response must be an object")

    meta = payload.get("meta")
    if not isinstance(meta, dict):
        fail(f"{name}: response meta must be an object")

    request_id = meta.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        fail(f"{name}: response meta.request_id is missing")

    if meta.get("partial") is True:
        fail(f"{name}: successful response is unexpectedly partial")

    return payload, raw


loaded: dict[str, dict[str, Any]] = {}
response_evidence: list[dict[str, Any]] = []

for name in response_names:
    payload, raw = load_response(name)
    loaded[name] = payload
    status_text = (
        evidence_directory / f"{name}.status"
    ).read_text(encoding="utf-8").strip()

    response_evidence.append(
        {
            "name": name,
            "http_status": int(status_text),
            "request_id": payload["meta"]["request_id"],
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "response_byte_size": len(raw),
        }
    )

list_data = loaded["list"].get("data")
if not isinstance(list_data, list):
    fail("list: data must be a JSON array")

page = loaded["list"].get("page")
if not isinstance(page, dict):
    fail("list: page metadata must be present")

if page.get("page_size") != 25:
    fail("list: page_size does not match the requested bound")


def required_summary(payload_name: str) -> dict[str, Any]:
    data = loaded[payload_name].get("data")
    if not isinstance(data, dict):
        fail(f"{payload_name}: data must be an object")
    return data


document_summary = required_summary("document_summary")
sync_summary = required_summary("sync_run")
report_summary = required_summary("report_digest")
latest_summary = required_summary("latest")

full_data = loaded["document_full"].get("data")
if not isinstance(full_data, dict):
    fail("document_full: data must be an object")

full_summary = full_data.get("summary")
if not isinstance(full_summary, dict):
    fail("document_full: data.summary must be an object")

report_payload = full_data.get("report_payload")
if not isinstance(report_payload, dict):
    fail("document_full: report_payload must be an object")

exact_summaries = {
    "document_summary": document_summary,
    "document_full": full_summary,
    "sync_run": sync_summary,
    "report_digest": report_summary,
}

identity_fields = (
    "document_id",
    "sync_run_id",
    "report_digest",
    "document_digest",
    "evidence_type",
    "evidence_classification",
)

for name, summary in exact_summaries.items():
    if summary.get("document_id") != expected_document_id:
        fail(f"{name}: document_id mismatch")
    if summary.get("sync_run_id") != expected_sync_run_id:
        fail(f"{name}: sync_run_id mismatch")
    if summary.get("report_digest") != expected_report_digest:
        fail(f"{name}: report_digest mismatch")
    if summary.get("document_digest") != expected_document_digest:
        fail(f"{name}: document_digest mismatch")
    if summary.get("evidence_type") != "CUSTOMER_GRAPH_SANDBOX_RUN":
        fail(f"{name}: evidence_type mismatch")
    if summary.get("evidence_classification") != "SANDBOX_VALIDATED":
        fail(f"{name}: evidence_classification mismatch")
    if summary.get("idempotent") is not True:
        fail(f"{name}: idempotency evidence is not true")

reference = document_summary
for name, summary in exact_summaries.items():
    for field_name in identity_fields:
        if summary.get(field_name) != reference.get(field_name):
            fail(f"{name}: cross-route identity mismatch for {field_name}")

if report_payload.get("report_digest") not in (None, expected_report_digest):
    fail("document_full: embedded report digest mismatch")

latest_executed_at = latest_summary.get("executed_at")
if not isinstance(latest_executed_at, str) or not latest_executed_at:
    fail("latest: executed_at is missing")

summary = {
    "schema_version": "1.0",
    "validation_type": "GRAPH_EVIDENCE_API_SANDBOX",
    "status": "SANDBOX_VALIDATED",
    "process_exit_code": 0,
    "base_url": os.environ["BASE_URL"],
    "validated_at": datetime.now(UTC).isoformat(timespec="microseconds"),
    "document_id": expected_document_id,
    "sync_run_id": expected_sync_run_id,
    "report_digest": expected_report_digest,
    "document_digest": expected_document_digest,
    "routes_validated": len(response_names),
    "responses": response_evidence,
}

summary_path = evidence_directory / "validation_summary.json"
summary_path.write_text(
    json.dumps(
        summary,
        ensure_ascii=True,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)

print(
    json.dumps(
        {
            "evidence_output": str(summary_path),
            "process_exit_code": 0,
            "routes_validated": len(response_names),
            "status": "SANDBOX_VALIDATED",
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
)
PY
