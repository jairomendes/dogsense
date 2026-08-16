#!/usr/bin/env bash
set -Eeuo pipefail

env_file="${1:-.env}"
if [[ ! -f "$env_file" ]]; then
  echo "Erro: $env_file não existe. Execute 'make setup' primeiro." >&2
  exit 1
fi

value_for() {
  local key="$1"
  awk -F= -v wanted="$key" '$1 == wanted { sub(/^[^=]*=/, ""); print; found=1 } END { if (!found) print "" }' "$env_file" | tail -n 1
}

missing=()
require_nonempty() {
  local key="$1"
  if [[ -z "$(value_for "$key")" ]]; then
    missing+=("$key")
  fi
}

require_nonplaceholder() {
  local key="$1"
  local value
  value="$(value_for "$key")"
  if [[ -z "$value" || "$value" == *change-me* || "$value" == dogsense-local-* || "$value" == dogsense-demo-* ]]; then
    missing+=("$key (valor exclusivo do ambiente)")
  fi
}

require_nonempty_file() {
  local key="$1"
  local path
  path="$(value_for "$key")"
  if [[ -z "$path" || "$path" != /* || ! -f "$path" || ! -s "$path" ]]; then
    missing+=("$key (caminho absoluto para arquivo local existente e não vazio)")
  fi
}

case "$(value_for DOGSENSE_AI_PROVIDER)" in
  gemini) require_nonempty GEMINI_API_KEY; require_nonempty GEMINI_MODEL ;;
esac
case "$(value_for SNOWFLAKE_MODE)" in
  real) require_nonempty SNOWFLAKE_ACCOUNT; require_nonempty SNOWFLAKE_USER; require_nonempty SNOWFLAKE_WAREHOUSE; require_nonempty_file SNOWFLAKE_PRIVATE_KEY_HOST_PATH; require_nonplaceholder ANALYTICS_HMAC_KEY ;;
esac
case "$(value_for ELEVENLABS_MODE)" in
  real) require_nonempty ELEVENLABS_API_KEY; require_nonempty ELEVENLABS_VOICE_ID; require_nonempty ELEVENLABS_MODEL_ID ;;
esac
case "$(value_for SOLANA_MODE)" in
  real) require_nonempty SOLANA_RPC_URL; require_nonempty_file SOLANA_KEYPAIR_HOST_PATH ;;
esac

if ((${#missing[@]} > 0)); then
  echo "Configuração real incompleta. Defina, sem compartilhar os valores:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  exit 1
fi

echo "Preflight das integrações habilitadas concluído; nenhum segredo foi exibido."
