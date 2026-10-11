#!/usr/bin/env bash
# Start the local OpenTelLM stack. Free: everything runs in Docker on your
# machine, and nothing calls OpenAI, Anthropic or any cloud service.
#
#   bash scripts/dev-up.sh          # first run: creates .env, builds, starts
#   bash scripts/smoke-test.sh      # then check the whole pipeline end to end
#   docker compose down             # stop (add -v to also delete the data)
#
# On the first run this creates .env from .env.example with fresh random
# secrets. If an existing .env still has an empty secret or one of the old
# published placeholders, those lines are replaced (the old file is kept as
# .env.bak). Changing API_KEY_PEPPER invalidates existing API keys, which is
# fine for a dev setup.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker with the compose plugin is required (Docker Desktop includes it)." >&2
  exit 1
fi

rand_hex() {  # 32 random bytes as 64 hex chars, without needing openssl
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    od -An -tx1 -N32 /dev/urandom | tr -d ' \n'
  fi
}

needs_secret() {  # $1 = variable name; true if missing, empty or a placeholder
  local line value
  line=$(grep -E "^$1=" .env | tail -n 1 || true)
  value=${line#*=}
  [[ -z "$value" ]] && return 0
  shopt -s nocasematch
  if [[ "$value" == *change-me* || "$value" == *change_me* || "$value" == *replace_me* \
        || "$value" == *replace-me* || "$value" == "opentelm-key-secret" ]]; then
    shopt -u nocasematch; return 0
  fi
  shopt -u nocasematch
  return 1
}

set_secret() {  # $1 = name, $2 = value; replace the line or append it
  if grep -qE "^$1=" .env; then
    # | as the sed delimiter: hex values never contain it
    sed -i.tmp -E "s|^$1=.*$|$1=$2|" .env && rm -f .env.tmp
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
else
  cp .env .env.bak
fi

changed=0
for name in JWT_SECRET_KEY API_KEY_PEPPER; do
  if needs_secret "$name"; then
    set_secret "$name" "$(rand_hex)"
    echo "Generated a random $name in .env"
    changed=1
  fi
done
[[ $changed == 0 && -f .env.bak ]] && rm -f .env.bak

echo "Building and starting containers (the first build takes a few minutes)..."
docker compose up -d --build

echo -n "Waiting for the dashboard"
for _ in $(seq 1 90); do
  if curl -fsS -o /dev/null http://localhost:3000/login 2>/dev/null; then
    echo
    echo
    echo "OpenTelLM is running:"
    echo "  Dashboard   http://localhost:3000"
    echo "  Ingest API  http://localhost:8000   (point the SDKs here)"
    echo "  Auth API    http://localhost:8001/docs"
    echo
    echo "Next: bash scripts/smoke-test.sh"
    echo "It checks the whole pipeline and prints a login for the dashboard."
    exit 0
  fi
  echo -n "."
  sleep 2
done
echo
echo "The dashboard didn't answer within 3 minutes. See: docker compose ps; docker compose logs dashboard" >&2
exit 1
