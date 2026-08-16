#!/usr/bin/env bash
set -Eeuo pipefail

url="${1:-http://127.0.0.1:8000/health/ready}"
attempts="${2:-40}"

if ! [[ "$attempts" =~ ^[1-9][0-9]*$ ]]; then
  echo "Erro: attempts deve ser um inteiro positivo." >&2
  exit 2
fi

for ((attempt = 1; attempt <= attempts; attempt++)); do
  if curl --silent --show-error --fail --max-time 2 "$url" >/dev/null 2>&1; then
    echo "Saudável: $url"
    exit 0
  fi
  sleep 1
done

echo "Timeout aguardando $url" >&2
exit 1

