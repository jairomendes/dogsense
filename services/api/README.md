# DogSense API

Backend FastAPI do DogSense Live. O modo padrão é uma demonstração determinística,
sem PostgreSQL e sem chamadas externas. O seed automático fornece um cachorro, uma
câmera, uma sessão e um evento encerrado.

## Execução local

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Abra `http://localhost:8000/docs`. O bootstrap está em
`GET /api/v1/demo/bootstrap`; os aliases `demo-dog`, `demo-camera`,
`demo-session` e `demo-event` também são aceitos.

## Modos e configuração

| Variável | Padrão | Uso |
|---|---|---|
| `DOGSENSE_DEMO_MODE` | `true` | Seed idempotente e fixtures determinísticas |
| `STORE_BACKEND` | `memory` | `memory` ou `postgres` |
| `POSTGRES_DSN` | — | Obrigatório para `STORE_BACKEND=postgres` |
| `AUTH_REQUIRED` | `false` | Exige Bearer token/JWT na API e WebSocket |
| `DOGSENSE_API_TOKEN` | `demo-local-token` | Token local, aceito apenas fora de produção |
| `INTERNAL_API_TOKEN` | `dogsense-worker-demo-token` | Protege a ingestão do worker |
| `CAMERA_ADAPTER` | `fake` | `fake` ou `ffprobe` |
| `MEDIAMTX_MODE` | `fake` | `fake` ou `real` |
| `MEDIAMTX_API_URL` | `http://mediamtx:9997` | Control API privada do MediaMTX |
| `SNOWFLAKE_MODE` | `fake` | `fake` ou `real` |
| `ELEVENLABS_MODE` | `fake` | `fake` ou `real` |
| `SOLANA_MODE` | `fake` | `fake` ou `real` (sempre Devnet no MVP) |
| `AUDIO_DIR` | `/tmp/dogsense-audio` | Volume protegido; áudio expira em 24 h |

No modo PostgreSQL, a implementação usa um snapshot JSONB atômico e recuperável,
adequado ao processo único do MVP. Uma implantação horizontal deve substituí-lo
por tabelas normalizadas com locks por linha.

Credenciais RTSP são criptografadas antes da persistência e nunca retornadas. A
URL pública é reduzida a `rtsp(s)://host[:porta]/***`. Frames e vídeo não são
gravados por esta API.

## Ingestão do worker

`POST /api/v1/internal/analyses` exige `X-Internal-Token` ou Bearer interno.
`analysis_id` é a chave de idempotência; `transition_seq` e `captured_at` impedem
mensagens atrasadas. A API persiste estado/evento antes do broadcast WebSocket.

## Testes

```bash
pytest
pytest --cov=app --cov-report=term-missing
```
