#!/usr/bin/env bash
# Fill a freshly copied .env with local development values.
#
# These belong in .env (gitignored), never in .env.example: taskbook §7 keeps
# the template free of values so the CI secret scan stays meaningful, and a
# generated JWT secret must never be shared between developers anyway.
#
# Provider API keys are deliberately left blank — USE_MOCK_PROVIDERS=true runs
# the whole pipeline without them (§170).
set -euo pipefail

ENV_FILE="${1:-.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE does not exist" >&2
  exit 1
fi

# Replace `KEY=` (empty value only) with `KEY=value`. Never overwrites a value
# the developer has already set.
set_if_empty() {
  local key="$1" value="$2"
  if grep -qE "^${key}=$" "$ENV_FILE"; then
    # `|` as the delimiter: values contain `/` and `:`.
    sed -i.bak "s|^${key}=$|${key}=${value}|" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"
  fi
}

if command -v openssl > /dev/null 2>&1; then
  jwt_secret="$(openssl rand -hex 32)"
else
  jwt_secret="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi

set_if_empty "JWT_SECRET" "$jwt_secret"

# Match the credentials docker-compose gives MinIO.
set_if_empty "S3_ACCESS_KEY_ID" "minioadmin"
set_if_empty "S3_SECRET_ACCESS_KEY" "minioadmin"

echo "Local development values written to $ENV_FILE"
