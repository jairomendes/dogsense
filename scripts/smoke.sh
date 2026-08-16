#!/usr/bin/env bash
set -Eeuo pipefail

api_base="${API_BASE_URL:-http://127.0.0.1:8000}"
web_base="${WEB_BASE_URL:-http://127.0.0.1:3000}"
video_url="${VIDEO_URL:-http://127.0.0.1:8889/dog-camera/whep}"
local_token="${DOGSENSE_API_TOKEN:-demo-local-token}"

curl --silent --show-error --fail --max-time 5 "$api_base/health/live" >/dev/null
ready_payload="$(curl --silent --show-error --fail --max-time 5 "$api_base/health/ready")"
if [[ "$ready_payload" != *'"status":"ready"'* ]]; then
  echo "Smoke falhou: readiness respondeu sem status=ready." >&2
  exit 1
fi

curl \
  --silent \
  --show-error \
  --fail \
  --max-time 5 \
  --header "Authorization: Bearer $local_token" \
  "$api_base/api/v1/integrations/status" >/dev/null

curl --silent --show-error --fail --max-time 5 "$web_base/" >/dev/null

whep_status="$(
  curl \
    --silent \
    --show-error \
    --output /dev/null \
    --write-out '%{http_code}' \
    --max-time 5 \
    --request OPTIONS \
    "$video_url"
)"
if [[ "$whep_status" != "200" && "$whep_status" != "204" ]]; then
  echo "Smoke falhou: sinalização WHEP respondeu HTTP $whep_status." >&2
  exit 1
fi

echo "Smoke aprovado: API live/ready, integrações, dashboard e sinalização WHEP responderam."
