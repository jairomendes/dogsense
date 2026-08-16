#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

if ! command -v docker >/dev/null 2>&1; then
  echo "Erro: Docker não foi encontrado. Instale Docker Engine/Desktop com Compose v2." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Erro: o plugin Docker Compose v2 não está disponível." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Criado .env com valores locais seguros e integrações fake."
else
  echo ".env já existe; nenhum valor foi sobrescrito."
fi

mkdir -p secrets
chmod 700 secrets

docker compose config --quiet
echo "Preflight concluído."
