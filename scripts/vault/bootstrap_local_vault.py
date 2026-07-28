#!/usr/bin/env python3
"""Initialize, unseal, configure, and seed the repository-local Vault service."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / ".vault-local"
INIT_FILE = STATE_DIR / "init.json"
TOKEN_FILE = STATE_DIR / "return-platform.token"
ENV_FILE = ROOT / ".env"
VAULT_ADDR = os.environ.get("PLATFORM_VAULT_ADDRESS", "http://127.0.0.1:8200").rstrip(
    "/"
)


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def is_placeholder_secret(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or normalized == "vault-resolved"
        or normalized.startswith("placeholder")
        or normalized.endswith("changeme")
        or normalized.endswith("change-me")
    )


def request_json(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    allowed_statuses: tuple[int, ...] = (200, 204),
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Vault-Token"] = token
    request = urllib.request.Request(
        f"{VAULT_ADDR}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read()
            if response.status not in allowed_statuses:
                raise RuntimeError(f"Vault returned HTTP {response.status} for {path}")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        if exc.code in allowed_statuses:
            payload = exc.read()
            return json.loads(payload) if payload else {}
        raise RuntimeError(
            f"Vault request failed with HTTP {exc.code} for {path}"
        ) from exc


def wait_for_vault() -> dict[str, Any]:
    last_error: Exception | None = None
    for _ in range(60):
        try:
            return request_json(
                "GET",
                "/v1/sys/health?standbyok=true&sealedcode=200&uninitcode=200",
                allowed_statuses=(200,),
            )
        except Exception as exc:  # startup retry boundary
            last_error = exc
            time.sleep(1)
    raise RuntimeError("Vault did not become reachable") from last_error


def write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def require_value(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise RuntimeError(f"Required environment value {key} is missing")
    return value


def parse_list(values: dict[str, str], key: str) -> tuple[str, ...]:
    raw = values.get(key, "").strip()
    if not raw:
        return ()
    if raw.startswith("["):
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise RuntimeError(f"Environment value {key} must be a JSON list")
        return tuple(str(item).strip() for item in parsed if str(item).strip())
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def put_kv(root_token: str, secret_path: str, values: dict[str, str]) -> None:
    try:
        current = request_json(
            "GET",
            f"/v1/secret/data/{secret_path}",
            token=root_token,
            allowed_statuses=(200,),
        )
        current_values = current.get("data", {}).get("data", {})
        if isinstance(current_values, dict) and all(
            current_values.get(key) == value for key, value in values.items()
        ):
            return
    except RuntimeError:
        pass
    request_json(
        "POST",
        f"/v1/secret/data/{secret_path}",
        token=root_token,
        body={"data": values},
        allowed_statuses=(200, 204),
    )


def resolve_bootstrap_secret(
    root_token: str,
    secret_path: str,
    field_name: str,
    configured_value: str,
) -> str:
    if not is_placeholder_secret(configured_value):
        return configured_value
    try:
        current = request_json(
            "GET",
            f"/v1/secret/data/{secret_path}",
            token=root_token,
            allowed_statuses=(200,),
        )
        stored = current.get("data", {}).get("data", {}).get(field_name)
        if (
            isinstance(stored, str)
            and not is_placeholder_secret(stored)
            and len(stored.encode("utf-8")) >= 32
        ):
            return stored
    except RuntimeError:
        pass
    return os.urandom(32).hex()


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    health = wait_for_vault()

    if not bool(health.get("initialized")):
        init_payload = request_json(
            "POST",
            "/v1/sys/init",
            body={"secret_shares": 1, "secret_threshold": 1},
        )
        write_private(INIT_FILE, json.dumps(init_payload, indent=2))
    elif not INIT_FILE.exists():
        raise RuntimeError(
            "Vault is initialized but local unseal state is missing. Restore .vault-local/init.json "
            "or reset the Vault volume explicitly."
        )

    init_payload = json.loads(INIT_FILE.read_text(encoding="utf-8"))
    unseal_key = str(init_payload["keys_base64"][0])
    root_token = str(init_payload["root_token"])

    health = wait_for_vault()
    if bool(health.get("sealed")):
        request_json("POST", "/v1/sys/unseal", body={"key": unseal_key})

    mounts = request_json("GET", "/v1/sys/mounts", token=root_token)
    if "secret/" not in mounts:
        request_json(
            "POST",
            "/v1/sys/mounts/secret",
            token=root_token,
            body={"type": "kv", "options": {"version": "2"}},
            allowed_statuses=(200, 204),
        )

    policy = """
path "secret/data/production/*" {
  capabilities = ["create", "read", "update"]
}
path "secret/metadata/production/*" {
  capabilities = ["read", "list", "delete"]
}
path "secret/delete/production/*" {
  capabilities = ["update"]
}
""".strip()
    request_json(
        "PUT",
        "/v1/sys/policies/acl/return-platform",
        token=root_token,
        body={"policy": policy},
        allowed_statuses=(200, 204),
    )

    app_token = (
        TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else ""
    )
    token_valid = False
    if app_token:
        try:
            request_json("GET", "/v1/auth/token/lookup-self", token=app_token)
            token_valid = True
        except RuntimeError:
            token_valid = False
    if not token_valid:
        token_response = request_json(
            "POST",
            "/v1/auth/token/create-orphan",
            token=root_token,
            body={
                "policies": ["return-platform"],
                "display_name": "return-platform-linux-host",
                "ttl": "720h",
                "renewable": True,
            },
        )
        app_token = str(token_response["auth"]["client_token"])
        write_private(TOKEN_FILE, app_token + "\n")

    env = read_env_file(ENV_FILE)
    mongo_user = env.get("MONGO_ROOT_USERNAME", "mongoadmin")
    mongo_password = require_value(env, "MONGO_ROOT_PASSWORD")
    encoded_user = urllib.parse.quote_plus(mongo_user)
    encoded_password = urllib.parse.quote_plus(mongo_password)
    host_base = f"mongodb://{encoded_user}:{encoded_password}@127.0.0.1:27017"
    container_base = f"mongodb://{encoded_user}:{encoded_password}@mongodb:27017"
    host_common = "authSource=admin&directConnection=true"
    container_common = "authSource=admin&replicaSet=rs0"
    put_kv(
        root_token,
        "production/data-sources/mongodb",
        {
            "dsn": f"{host_base}/return_platform?{host_common}",
            "source_dsn": f"{host_base}/return_source?{host_common}",
            "container_dsn": f"{container_base}/return_platform?{container_common}",
            "container_source_dsn": f"{container_base}/return_source?{container_common}",
        },
    )
    put_kv(
        root_token,
        "production/data-sources/neo4j",
        {"password": require_value(env, "GRAPH_PASSWORD")},
    )
    put_kv(
        root_token,
        "production/data-sources/valkey",
        {"password": require_value(env, "VALKEY_PASSWORD")},
    )
    put_kv(
        root_token,
        "production/data-sources/sqlserver",
        {"password": require_value(env, "MSSQL_SA_PASSWORD")},
    )
    validation_key = resolve_bootstrap_secret(
        root_token,
        "production/platform/validation",
        "fingerprint_key",
        env.get("PLATFORM_VALIDATION_FINGERPRINT_KEY", "").strip(),
    )
    put_kv(
        root_token,
        "production/platform/validation",
        {"fingerprint_key": validation_key},
    )
    contact_lookup_key = resolve_bootstrap_secret(
        root_token,
        "production/platform/contact-lookup",
        "hmac_key",
        env.get("PLATFORM_CONTACT_LOOKUP_HMAC_KEY", "").strip(),
    )
    put_kv(
        root_token,
        "production/platform/contact-lookup",
        {"hmac_key": contact_lookup_key},
    )
    for provider in ("google", "nvidia", "openai", "anthropic"):
        keys = parse_list(env, f"PLATFORM_{provider.upper()}_API_KEYS")
        for index, api_key in enumerate(keys, start=1):
            if is_placeholder_secret(api_key):
                continue
            put_kv(
                root_token,
                f"production/ai/{provider}/credentials/key-{index}",
                {"api_key": api_key},
            )

    print("vault_status=READY")
    print(f"application_token_file={TOKEN_FILE}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"vault_bootstrap_failed={type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
