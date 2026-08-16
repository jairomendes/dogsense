#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

bash scripts/bootstrap.sh

if grep -Eq '^[[:space:]]*DOGSENSE_DEMO_MODE=(false|0|no)[[:space:]]*$' .env; then
  echo "Erro: make demo exige DOGSENSE_DEMO_MODE=true no arquivo .env." >&2
  exit 1
fi

docker compose --profile controlled-video up --build --detach

if ! bash scripts/wait-for-health.sh "http://127.0.0.1:${API_PORT:-8000}/health/ready" 60; then
  docker compose ps
  echo "Use 'make logs' para diagnóstico." >&2
  exit 1
fi

echo
echo "DogSense demo está pronto:"
echo "  Dashboard: http://localhost:${WEB_PORT:-3000}"
echo "  API docs:  http://localhost:${API_PORT:-8000}/docs"
echo "  WebRTC:    http://localhost:${MEDIAMTX_WEBRTC_PORT:-8889}/dog-camera/whep"
echo "Os estados e provedores estão em modo simulado; nenhuma credencial externa é necessária."

