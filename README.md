# DogSense Live

DogSense Live transforma um stream de câmera em uma leitura temporal e
explicável de comportamento canino. A interface apresenta **estado provável**,
atividade, confiança, sinais observáveis e uma linha do tempo. O produto não
faz diagnóstico veterinário nem afirma conhecer com certeza a emoção do animal.

O repositório inclui um caminho de demonstração determinístico que sobe sem
credenciais. Google AI, Snowflake, ElevenLabs e Solana podem ser habilitados
separadamente depois que o fluxo local estiver validado.

## Início rápido: demo sem credenciais

Pré-requisitos:

- Docker Engine ou Docker Desktop com Docker Compose v2;
- `make` e `curl` no host;
- portas TCP `3000`, `8000`, `8554`, `8889` e UDP `8189` livres em localhost;
- acesso à internet apenas na primeira execução, para obter imagens e dependências.

Execute:

```bash
make demo
```

O comando:

1. cria `.env` a partir de `.env.example` somente se ele ainda não existir;
2. mantém todas as integrações em `fake`;
3. sobe PostgreSQL, MediaMTX, API, worker e web;
4. publica um padrão de vídeo sintético, gerado em memória;
5. aguarda o readiness da API.

Abra:

- dashboard: <http://localhost:3000>;
- OpenAPI: <http://localhost:8000/docs>;
- readiness: <http://localhost:8000/health/ready>.

Os resultados comportamentais da demo vêm de
[`demo/scenarios/demo-tour.json`](demo/scenarios/demo-tour.json), não do padrão
visual sintético. A interface deve identificá-los como simulados. Nenhum vídeo é
gravado, e nenhuma requisição é enviada aos quatro provedores externos.

Para encerrar sem apagar dados:

```bash
make down
```

## Comandos

| Comando | Efeito |
|---|---|
| `make setup` | cria `.env` sem sobrescrever e valida o Compose |
| `make up` | sobe o stack sem a câmera sintética |
| `make demo` | sobe o stack determinístico completo |
| `make health` | consulta readiness |
| `make smoke` | verifica API e dashboard |
| `make logs` | acompanha logs com rotação local |
| `make ps` | mostra saúde dos contêineres |
| `make migrate` | executa a migração idempotente da API |
| `make seed` | reaplica o seed de demonstração |
| `make test` | executa testes de API, worker e web |
| `make lint` | executa as verificações estáticas disponíveis |
| `make psql` | abre o PostgreSQL sem publicar sua porta |
| `make down` | para contêineres, preservando volumes |

`docker compose up --build` também funciona a partir da raiz. Ele sobe o stack
padrão; para incluir a câmera sintética use o profile `controlled-video`.

## Como a demo evolui

O cenário principal percorre, com tempo relativo ao início do worker:

| Instante | Resultado fake | O que observar |
|---:|---|---|
| 0 s | `relaxed` / `resting` | sinais de pouco movimento e postura solta |
| 10 s | `alert` / `standing` | transição somente após estabilização |
| 22 s | `stress_signals` / `pacing` | linguagem não diagnóstica e evidências |
| 38 s | cachorro fora do quadro | `indeterminate`/estado técnico apropriado |
| 50 s | `engaged` / `playing` | retorno do cachorro e novo evento |
| 64 s | `relaxed` / `resting` | encerramento da volta; o loop reinicia aos 76 s |

Para manter apenas um estado, altere no `.env`:

```dotenv
DOGSENSE_FAKE_SCENARIO_PATH=/demo/scenarios/relaxed-loop.json
```

Depois reinicie somente o worker:

```bash
docker compose restart video-worker
```

## Modos de execução

As integrações são independentes. Uma falha em analytics, voz ou blockchain não
deve interromper o vídeo nem o estado ao vivo.

| Variável | Padrão | Valor real |
|---|---|---|
| `DOGSENSE_AI_PROVIDER` | `fake` | `gemini` |
| `SNOWFLAKE_MODE` | `fake` | `real` |
| `ELEVENLABS_MODE` | `fake` | `real` |
| `SOLANA_MODE` | `fake` | `real` |

Antes de uma execução real:

```bash
make setup
# edite .env localmente; não cole segredos em issue, chat ou terminal gravado
make preflight-real
docker compose up --build --detach
```

O preflight só confirma presença; ele nunca imprime os valores.

### Google AI

Defina `DOGSENSE_AI_PROVIDER=gemini`, `GEMINI_API_KEY` e `GEMINI_MODEL`. O modelo não é
fixado no código. Cada análise é associada a
`behavior-observer-v1`/`behavior-analysis-v1`, tem timeout de oito segundos e é
validada antes de afetar o estado.

Somente a janela amostrada é enviada. Frames ficam em memória e não devem ser
incluídos em logs, traces ou respostas da API.

### Snowflake

1. Crie uma conta/warehouse de sandbox e um usuário de menor privilégio.
2. Execute, nessa ordem:

```text
snowflake/migrations/001_behavior_schema.sql
snowflake/views/001_dog_state_hourly.sql
snowflake/views/002_dog_state_daily.sql
```

3. Defina `SNOWFLAKE_MODE=real`, conta, usuário, warehouse e role. Salve a chave
   PEM fora do repositório e indique seu caminho host absoluto em
   `SNOWFLAKE_PRIVATE_KEY_HOST_PATH`; ela será montada somente para leitura.
4. Gere `ANALYTICS_HMAC_KEY` exclusivamente para pseudonimização analítica.

O passo a passo completo, incluindo key-pair RSA e grants, está em
[`docs/snowflake-setup.md`](docs/snowflake-setup.md).

Eventos são gravados primeiro no PostgreSQL e enviados por `MERGE` idempotente
em `EVENT_ID`. Snowflake recebe IDs pseudonimizados e metadados consolidados;
nunca recebe URL RTSP, nome do tutor, frames ou vídeo.

### ElevenLabs

Defina `ELEVENLABS_MODE=real`, `ELEVENLABS_API_KEY`,
`ELEVENLABS_VOICE_ID` e `ELEVENLABS_MODEL_ID`. Apenas texto gerado por templates
permitidos é enviado. Texto livre do modelo não é narrado. O áudio local expira
em até 24 horas; a interface textual continua funcionando em falha.

### Solana Devnet

Use somente Devnet. Defina `SOLANA_MODE=real`, `SOLANA_RPC_URL`,
`SOLANA_NETWORK=devnet` e `SOLANA_KEYPAIR_HOST_PATH` com o caminho host absoluto
para um keypair de saldo
mínimo fora do repositório; o arquivo é montado somente para leitura.
Somente o hash SHA-256 e um identificador técnico são publicados. Nome, endereço,
imagem, URL e ID previsível do animal são proibidos no memo.

Não use este MVP com Mainnet.

### Câmera RTSP real

Cadastre a câmera pelo dashboard/API, que guarda usuário e senha separados,
criptografa a senha e configura o path interno `dog-camera` pela Control API não
publicada do MediaMTX. Para teste real, use `CAMERA_ADAPTER=ffprobe`. Prefira RTSP
sobre TCP e uma conta da câmera criada só para leitura. A URL completa nunca deve
aparecer em logs.

Para acesso a partir de outro dispositivo, `DOGSENSE_BIND_HOST=0.0.0.0` amplia a
superfície de rede. Faça isso apenas em uma rede confiável, com JWT, CORS/Origin
restrito e `MEDIAMTX_WEBRTC_ADDITIONAL_HOSTS` configurado. O token local de demo
é proibido em implantação pública.

## Privacidade por padrão

- gravação do MediaMTX está desabilitada;
- frames de inferência ficam apenas em memória;
- PostgreSQL não publica porta no host;
- serviços externos começam em modo fake;
- portas HTTP/RTSP ficam vinculadas a `127.0.0.1` por padrão;
- logs têm rotação e não podem conter frame, base64, token, chave ou URL RTSP;
- diretórios de mídia e secrets estão excluídos do Git;
- Solana recebe somente hashes e é publicamente imutável.

Antes de usar vídeo real, obtenha consentimento das pessoas potencialmente
filmadas, enquadre apenas a área necessária e revise a política de retenção de
cada provedor. Veja [`docs/privacy.md`](docs/privacy.md).

## Estrutura

```text
apps/web/                  dashboard responsivo
services/api/              FastAPI, PostgreSQL e integrações
services/video-worker/     captura, Google AI e motor temporal
packages/contracts/        schemas e tipos compartilhados
infra/                     Compose, MediaMTX e init PostgreSQL
snowflake/                 migrações, views e dados sintéticos
demo/scenarios/            respostas determinísticas versionadas
docs/                      arquitetura, privacidade e operação
docs/diagrams/             diagramas Mermaid gerados a partir do código
scripts/                   bootstrap, preflight e smoke test seguros
```

Documentação adicional:

- [`docs/architecture.md`](docs/architecture.md) — componentes, redes, fluxos e diagramas;
- [`docs/prompt-design.md`](docs/prompt-design.md) — limites do observador visual;
- [`docs/demo-script.md`](docs/demo-script.md) — roteiro de apresentação;
- [`docs/runbook.md`](docs/runbook.md) — diagnóstico e recuperação;
- [`docs/snowflake-setup.md`](docs/snowflake-setup.md) — conta, key-pair e `.env`;
- [`PRD Técnico — DogSense Live.md`](PRD%20T%C3%A9cnico%20%E2%80%94%20DogSense%20Live.md) — especificação de produto;
- [`PLANO_DE_DESENVOLVIMENTO_IMPLEMENTACAO_E_TESTES.md`](PLANO_DE_DESENVOLVIMENTO_IMPLEMENTACAO_E_TESTES.md) — plano e gates.

## Diagnóstico rápido

```bash
make ps
make health
docker compose logs --tail=200 api video-worker mediamtx
```

- API não fica ready: confira primeiro `postgres` e migrações.
- Player sem vídeo na demo: confirme que `demo-camera` está no profile e que o
  path `dog-camera` aparece nos logs do MediaMTX.
- Estado não muda: reinicie o worker para zerar o relógio relativo do cenário.
- WebRTC funciona apenas no host: configure host/candidato ICE conforme o
  [`docs/runbook.md`](docs/runbook.md), sem abrir o banco.
- Provedor real falha: volte apenas o respectivo modo para `fake` e reinicie API ou
  worker; o restante do fluxo deve permanecer disponível.

O runbook não inclui comandos automáticos para apagar volumes. Exclusão de dados
é uma ação irreversível e deve ser confirmada e direcionada ao volume exato.
