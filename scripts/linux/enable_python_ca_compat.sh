#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  return 0 2>/dev/null || exit 0
fi

if [[ "${RETURNS_ENABLE_PYTHON_CA_COMPAT:-true}" == "false" ]]; then
  return 0 2>/dev/null || exit 0
fi

_returns_ca_bundle="${RETURNS_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}"
_returns_ca_compat_dir="${RETURNS_PYTHON_CA_COMPAT_DIR:-/tmp/returns-python-ca-compat}"

if [[ ! -f "$_returns_ca_bundle" || ! -r "$_returns_ca_bundle" ]]; then
  echo "Python CA compatibility failed: CA bundle is not a readable file: $_returns_ca_bundle" >&2
  return 1 2>/dev/null || exit 1
fi

if ! command -v realpath >/dev/null 2>&1; then
  echo "Python CA compatibility failed: realpath is required." >&2
  return 1 2>/dev/null || exit 1
fi

_returns_ca_compat_dir="$(realpath -m -- "$_returns_ca_compat_dir")"
case "$_returns_ca_compat_dir" in
  /tmp/*) ;;
  *)
    echo "Python CA compatibility failed: runtime directory must be below /tmp." >&2
    return 1 2>/dev/null || exit 1
    ;;
esac

umask 077
mkdir -p -- "$_returns_ca_compat_dir"
if [[ ! -d "$_returns_ca_compat_dir" || -L "$_returns_ca_compat_dir" ]]; then
  echo "Python CA compatibility failed: runtime path is not a secure directory." >&2
  return 1 2>/dev/null || exit 1
fi
chmod 700 -- "$_returns_ca_compat_dir"

_returns_sitecustomize="$_returns_ca_compat_dir/sitecustomize.py"
_returns_sitecustomize_tmp="$(mktemp "$_returns_ca_compat_dir/.sitecustomize.py.XXXXXX")"
cat >"$_returns_sitecustomize_tmp" <<'PY'
"""Temporary Linux-only compatibility for Python's default TLS context."""

import ssl

_original_create_default_context = getattr(
    ssl,
    "_returns_original_create_default_context",
    ssl.create_default_context,
)
ssl._returns_original_create_default_context = _original_create_default_context


def _returns_create_default_context(*args, **kwargs):
    context = _original_create_default_context(*args, **kwargs)
    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict_flag:
        context.verify_flags &= ~strict_flag
    return context


ssl.create_default_context = _returns_create_default_context
ssl._create_default_https_context = _returns_create_default_context
PY
chmod 600 -- "$_returns_sitecustomize_tmp"
mv -f -- "$_returns_sitecustomize_tmp" "$_returns_sitecustomize"
chmod 600 -- "$_returns_sitecustomize"

case ":${PYTHONPATH:-}:" in
  *":$_returns_ca_compat_dir:"*) ;;
  *) export PYTHONPATH="$_returns_ca_compat_dir${PYTHONPATH:+:$PYTHONPATH}" ;;
esac
export SSL_CERT_FILE="$_returns_ca_bundle"
export REQUESTS_CA_BUNDLE="$_returns_ca_bundle"
export CURL_CA_BUNDLE="$_returns_ca_bundle"
export RETURNS_PYTHON_CA_COMPAT_ACTIVE=1

printf 'Linux Python CA compatibility active (bundle=%s, runtime=%s; verification retained).\n' \
  "$_returns_ca_bundle" "$_returns_ca_compat_dir"
