# Roteiro de demonstração

## Objetivo

Em cinco a sete minutos, mostrar vídeo contínuo, estado provável explicável,
estabilização temporal, timeline e degradação independente. O apresentador deve
dizer explicitamente que o cenário comportamental é simulado e que o padrão de
vídeo não foi analisado.

## Preparação (antes da apresentação)

```bash
make demo
make smoke
make ps
```

Confirme:

- dashboard em `http://localhost:3000`;
- todos os serviços healthy/running;
- `.env` com `DOGSENSE_DEMO_MODE=true` e quatro modos `fake`;
- browser e zoom testados na tela da apresentação;
- nenhuma aba, terminal ou log mostra `.env`/credenciais;
- três ensaios completos consecutivos.

Para reiniciar o relógio do tour:

```bash
docker compose restart video-worker
```

## Narrativa

### 0:00 — Problema e limite

“Uma câmera comum mostra pixels; DogSense organiza sinais observáveis ao longo do
tempo. A tela diz estado provável, nunca diagnóstico ou certeza emocional.”

Aponte `SIMULATED`/indicador equivalente antes de falar dos resultados.

### 0:40 — Repouso

Mostre vídeo, `resting`, `relaxed`, confiança e sinais `low_motion` e
`loose_body_posture`. Destaque que evidências textuais complementam a cor.

### 1:30 — Transição para alerta

Por volta de 10 s, o fake passa a `standing`/`alert`. Explique que o dashboard só
consolida após duas análises e margem suficiente, evitando oscilações de um frame.

### 2:20 — Sinais persistentes

Por volta de 22 s, mostre `pacing`/`stress_signals` e as evidências. Use a frase:
“São sinais relacionados a estresse que merecem contexto no vídeo, não um
diagnóstico de ansiedade.”

Acione leitura de voz. Em fake, o texto auditável deve continuar disponível mesmo
sem chamada externa. Crie um receipt do evento encerrado quando a UI permitir e
mostre hash/Devnet como simulado.

### 3:30 — Cachorro fora do quadro

Por volta de 38 s, mostre ausência de cachorro, queda de qualidade e ausência de
uma emoção fabricada. A timeline encerra o evento segundo a regra temporal.

### 4:20 — Recuperação

Por volta de 50 s, o cão simulado retorna como `engaged`/`playing`; depois volta a
repouso. Mostre que a timeline mantém eventos consolidados no PostgreSQL.

### 5:10 — Arquitetura e privacidade

Resuma: stream local no MediaMTX, frames em memória, evento primeiro no Postgres,
outbox para integrações. Snowflake recebe pseudônimos; ElevenLabs recebe template;
Solana recebe hash. Nenhuma gravação por padrão.

## Falha controlada opcional

Somente após o fluxo principal e com tempo disponível:

```bash
docker compose stop mediamtx demo-camera
```

Mostre que a UI permanece acessível e deve sinalizar câmera offline. Recupere:

```bash
docker compose start mediamtx demo-camera
```

Espere o stream retornar; não recarregue a página. Se a recuperação levar mais de
30 segundos, use o fallback abaixo em vez de investigar ao vivo.

## Fallbacks

| Problema | Ação curta | Mensagem honesta |
|---|---|---|
| estado não avançou | reinicie `video-worker` e comece no repouso | “Reiniciei o relógio determinístico.” |
| player sem vídeo | preserve o dashboard e use timeline/estado fake | “O relay está degradado; a análise simulada é independente.” |
| API não ready | mostre gravação de backup aprovada | “O ambiente local não ficou pronto a tempo.” |
| integração real instável | volte só seu `*_MODE=fake` | “O provedor foi isolado; o vídeo não depende dele.” |
| rede externa indisponível | mantenha todos os fakes | “A demo local não requer credenciais nem internet.” |

Não improvise com uma chave real, não abra `.env` em tela e não troque para
Mainnet. O objetivo é demonstrar o comportamento do produto com transparência.

## Encerramento

```bash
make down
```

O comando preserva banco e cache para investigação. Qualquer exclusão de volume
deve ocorrer depois, com confirmação explícita do alvo.

